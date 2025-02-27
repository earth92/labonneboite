from typing import Dict, Optional, Tuple, Union
from urllib.parse import urlencode

from flask import abort, Blueprint, make_response, redirect, render_template, request, url_for, jsonify
from flask_login import current_user
from sentry_sdk import start_transaction
from slugify import slugify

from labonneboite.common import activity, autocomplete, geocoding
from labonneboite.common import mapping as mapping_util
from labonneboite.common import pagination, pro, sorting
from labonneboite.common.load_data import load_related_rome, load_related_rome_areas
from labonneboite.common.locations import CityLocation, Location, NamedLocation
from labonneboite.common.models import UserFavoriteOffice
from labonneboite.common.refresh_peam_token import attempt_to_refresh_peam_token
from labonneboite.common.search import AudienceFilter, HiddenMarketFetcher
from labonneboite.common.util import get_enum_from_value
from labonneboite.conf import settings
from labonneboite.web.search.forms import make_company_search_form
from labonneboite.web.utils import fix_csrf_session

searchBlueprint = Blueprint('search', __name__)

RELATED_ROMES = load_related_rome() if settings.ENABLE_RELATED_ROMES else []
RELATED_ROMES_AREAS = load_related_rome_areas() if settings.ENABLE_RELATED_ROMES else []


@searchBlueprint.route('/suggest_job_labels')
def suggest_job_labels():
    term = request.args.get('term', '')
    suggestions = autocomplete.build_job_label_suggestions(term)
    return jsonify(suggestions)


@searchBlueprint.route('/suggest_locations')
def suggest_locations():
    """
    This route uses our internal search mechanism (elastic)
    It does not use the api-adresse.data.gouv.fr API

    Query string arguments:

        term (str)

    """
    term = request.args.get('term', '')
    suggestions = autocomplete.build_location_suggestions(term)
    return jsonify(suggestions)


@searchBlueprint.route('/autocomplete/locations')
def autocomplete_locations():
    """
    This route trys to get an address from api-adresse.data.gouv.fr API
    If this fails - e.g. the API is down, then we use our internal search mechanism (elastic)
    The api-adresse.data.gouv.fr API is more flexible as
    it allows the users to search based on an address, not just departments names

    Query string arguments:

        term (str)
    """
    term = request.args.get('term', '').strip()
    suggestions = []
    if term:
        suggestions = geocoding.get_coordinates(term, limit=autocomplete.MAX_LOCATIONS)
    if not suggestions:
        suggestions = autocomplete.build_location_suggestions(term)
        if suggestions:
            pass  # FIXME log BAN LIKELY DOWN event
    for suggestion in suggestions:
        suggestion['value'] = suggestion['label']
    return make_response(jsonify(suggestions))


@searchBlueprint.route('/job_slug_details')
def job_slug_details():
    result = []

    job_slug = request.args.get('job-slug', '')
    if not job_slug:
        return jsonify({'response': 'no job-slug given'}), 400

    slugs = job_slug.split(',')
    for slug in slugs:
        rome = mapping_util.SLUGIFIED_ROME_LABELS.get(slug)
        # Ignored unknown job slugs
        if rome:
            result.append({
                'rome_code': rome,
                'label': settings.ROME_DESCRIPTIONS.get(rome, ''),
            })

    return jsonify(result)


@searchBlueprint.route('/city_slug_details')
def city_slug_details():
    """
    Endpoint used by La Bonne Alternance only.

    Required parameter:
        city-slug (str): must take the form "slug-zipcode"

    Note that if the slug does not match the zipcode, the returned result will be incorrect.
    """
    result = {}

    city_slug = request.args.get('city-slug', '')

    if not city_slug:
        return jsonify({'response': 'no city-slug given'}), 400

    city = city_slug.split('-')
    zipcode = ''.join(city[-1:])
    city_location = CityLocation(zipcode, city_slug)

    if not city_location.is_location_correct:
        return jsonify({'response': f'no city found associated to the slug {city_slug}'}), 400

    result['city'] = {
        'name': city_location.name,
        'longitude': city_location.location.longitude,
        'latitude': city_location.location.latitude,
    }

    return jsonify(result)


@searchBlueprint.route('/city_code_details')
def city_code_details():
    """
    Endpoint used by La Bonne Alternance only.
    Required parameter:
        city-code (str)
    """
    result = {}

    city_code = request.args.get('city-code', '')
    if not city_code:
        return jsonify({'response': 'no city-code given'}), 400

    city = geocoding.get_city_by_commune_id(city_code)
    if not city:
        return jsonify({'response': f'no city found associated to the code {city_code}'}), 400

    result['city'] = {
        'name': city['name'],
        'slug': '{}-{}'.format(city['slug'], city['zipcode']),
        'longitude': city['coords']['lon'],
        'latitude': city['coords']['lat'],
    }

    return jsonify(result)


PARAMETER_FIELD_MAPPING = {
    'r': 'rome',
    'j': 'job',
    'ij': 'related_rome_initial',
    'd': 'distance',
    'tr': 'travel_mode',
    'l': 'location',
    'h': 'headcount',
    'naf': 'naf',
    'sort': 'sort',
    'from': 'from_number',
    'to': 'to_number',
    'p': 'audience',


}


def get_parameters(args):
    kwargs = {}

    for param, field_name in PARAMETER_FIELD_MAPPING.items():
        kwargs[field_name] = args.get(param, '')

    try:
        kwargs['distance'] = int(kwargs.get('distance'))
    except ValueError:
        kwargs['distance'] = settings.DISTANCE_FILTER_DEFAULT

    try:
        kwargs['headcount'] = int(kwargs.get('headcount'))
    except ValueError:
        kwargs['headcount'] = settings.HEADCOUNT_FILTER_DEFAULT

    try:
        kwargs['from_number'] = int(kwargs.get('from_number'))
    except ValueError:
        kwargs['from_number'] = 1

    try:
        kwargs['to_number'] = int(kwargs.get('to_number'))
    except ValueError:
        kwargs['to_number'] = kwargs['from_number'] + pagination.OFFICES_PER_PAGE - 1

    # Fix pagination when needed
    if not kwargs['from_number'] >= 1:
        kwargs['from_number'] = 1
    current_page_size = kwargs['to_number'] - kwargs['from_number'] + 1
    if current_page_size <= 0:  # this may happen when a 'out of bound' page is requested
        kwargs['to_number'] = kwargs['from_number'] + pagination.OFFICES_PER_PAGE - 1
    if current_page_size > pagination.OFFICES_MAXIMUM_PAGE_SIZE:
        kwargs['to_number'] = kwargs['from_number'] + pagination.OFFICES_MAXIMUM_PAGE_SIZE - 1

    # Fallback to default sorting.
    if kwargs.get('sort') not in sorting.SORT_FILTERS:
        kwargs['sort'] = sorting.SORT_FILTER_DEFAULT

    # prevent sorting by distance for departments
    if kwargs.get('sort') == sorting.SORT_FILTER_DISTANCE and 'departments' in args:
        kwargs['sort'] = sorting.SORT_FILTER_SMART

    # from value in GET to enum
    # audience filter defaults to ALL
    audience_str = kwargs.get('audience')
    if audience_str.isdigit():
        kwargs['audience'] = get_enum_from_value(AudienceFilter, int(audience_str), AudienceFilter.ALL)
    else:
        kwargs['audience'] = AudienceFilter.ALL

    # ensure PRO filters are never used in the public version
    if not pro.pro_version_enabled():
        kwargs['audience'] = AudienceFilter.ALL

    return kwargs


@searchBlueprint.route('/entreprises/<city>-<zipcode>/<occupation>')
def results(city, zipcode, occupation):
    """
    All this does is a redirect to the 'search.entreprises' view with
    city-related location parameters. This view is preserved so that older urls
    still work.
    """
    fix_csrf_session()
    params = request.args.copy()
    params['city'] = city
    params['zipcode'] = zipcode
    params['occupation'] = occupation

    redirect_url = url_for('search.entreprises', **params)
    return redirect(redirect_url)

# def add_nafs(rome_object):
#     nafs = mapping_util.nafs_for_rome(rome_object.get('rome'))
#     if(nafs):
#         rome_object['nafs'] = nafs
#     return rome_object


def add_descriptions(rome_object):
    description = settings.ROME_DESCRIPTIONS[rome_object.get('rome')]
    if (description):
        rome_object['description'] = description
    return rome_object


@searchBlueprint.route('/entreprises')
def entreprises():
    """
    This view takes arguments as a query string.

    Expected arguments are those returned by get_parameters and expected by the
    selected office search form.
    """
    fix_csrf_session()

    refresh_token_result = attempt_to_refresh_peam_token()
    if refresh_token_result["token_has_expired"]:
        return redirect(refresh_token_result["redirect_url"])

    # filter empty url params
    args = {key: request.args[key] for key in request.args if request.args[key] != ''}

    # get structured location data from url params
    location, named_location, departments = get_location(args)

    occupation = args.get('occupation', '')
    if not occupation and 'j' in args:
        suggestion = autocomplete.build_job_label_suggestions(args['j'], size=1)
        occupation = suggestion[0]['occupation'] if suggestion else None

    rome = mapping_util.SLUGIFIED_ROME_LABELS.get(occupation)
    job_doesnt_exist = not rome

    # Related romes
    if rome and settings.ENABLE_RELATED_ROMES:
        related_romes = None
        hide_suggestions = False
        if (named_location):
            with start_transaction(op='related_romes_get', name='rome_' + rome):
                related_area = RELATED_ROMES_AREAS.get(named_location.city_code, None)
                if (related_area):
                    # Hide suggestions for places which may have suggestions
                    hide_suggestions = True
                    romes = RELATED_ROMES.get(related_area, [])
                    if (rome in romes):
                        # Case where there are suggestions for this rome and this location
                        related_romes = romes.get(rome)
                        # related_romes = list(map(add_nafs, related_romes))
                        related_romes = list(map(add_descriptions, related_romes))
                        # sort and limit size
                        related_romes.sort(key=lambda rome_: -rome_.get('score'))
                        related_romes = related_romes[:settings.MAX_RELATED_ROMES]
    else:
        related_romes = []
        hide_suggestions = False

    # Build form
    form_kwargs = {key: val for key, val in list(args.items()) if val}
    form_kwargs['j'] = settings.ROME_DESCRIPTIONS.get(rome, occupation)

    if 'occupation' not in form_kwargs:
        form_kwargs['occupation'] = occupation

    if not form_kwargs.get('l') and named_location:
        # Override form location only if it is not available (e.g when user has
        # removed it from the url)
        form_kwargs['l'] = named_location.name

    if location:
        form_kwargs['lat'] = location.latitude
        form_kwargs['lon'] = location.longitude

    if departments:
        form_kwargs['departments'] = departments

    form = make_company_search_form(**form_kwargs)

    # Render different template if it's an ajax call
    is_xhr = False
    request_xhr_key = request.headers.get('X-Requested-With')
    if request_xhr_key and request_xhr_key == 'XMLHttpRequest':
        is_xhr = True
    template = 'search/results.html' if not is_xhr else 'search/results_content.html'

    activity_log_properties = dict(
        emploi=occupation,
        localisation={
            'nom': named_location.name if named_location else None,
            'ville': named_location.city if named_location else None,
            'codepostal': named_location.zipcode if named_location else None,
            'latitude': location.latitude if location else None,
            'longitude': location.longitude if location else None,
            'departments': departments if departments else None,
        },
    )

    # Stop here in case of invalid arguments
    if not form.validate() or job_doesnt_exist:
        log_search_activity(activity_log_properties)
        form.set_auto_focus_on_searched_job()
        return render_template(template, job_doesnt_exist=job_doesnt_exist, company_count=0, form=form)

    # Convert request arguments to fetcher parameters
    parameters = get_parameters(args)

    # Fetch offices and alternatives
    fetcher = HiddenMarketFetcher(
        location.longitude if location is not None else None,
        location.latitude if location is not None else None,
        departments=departments.split(',') if departments else None,
        romes=[rome],
        distance=parameters['distance'],
        travel_mode=parameters['travel_mode'],
        sort=parameters['sort'],
        from_number=parameters['from_number'],
        to_number=parameters['to_number'],
        audience=parameters['audience'],
        headcount=parameters['headcount'],
        naf=parameters['naf'],
        naf_codes=None,
        aggregate_by=['naf'],
    )
    alternative_rome_descriptions = []
    naf_codes_with_descriptions = []
    offices = []
    office_count = 0
    alternative_distances = {}

    # Aggregations
    offices, aggregations = fetcher.get_offices(add_suggestions=True)
    office_count = fetcher.office_count
    alternative_distances = fetcher.alternative_distances
    alternative_rome_descriptions = fetcher.get_alternative_rome_descriptions()

    # If a filter or more are selected, the aggregations returned by fetcher.get_offices()
    # will be filtered too... To avoid that, we are doing additionnal calls (one by filter activated)
    if aggregations:
        fetcher.update_aggregations(aggregations)

    # Generates values for the NAF filter
    # aggregations could be empty if errors or empty results
    if aggregations:
        for naf_aggregate in aggregations['naf']:
            naf_description = '%s (%s)' % (settings.NAF_CODES.get(naf_aggregate["code"]), naf_aggregate["count"])
            naf_codes_with_descriptions.append((naf_aggregate["code"], naf_description))

    # Pagination.
    pagination_manager = pagination.PaginationManager(
        office_count,
        fetcher.from_number,
        fetcher.to_number,
        request.full_path,
    )
    current_page = pagination_manager.get_current_page()

    form.naf.choices = [('', 'Tous les secteurs')] + sorted(naf_codes_with_descriptions, key=lambda t: t[1])
    form.validate()

    canonical_url = get_canonical_results_url(
        named_location.zipcode, named_location.city,
        occupation
    ) if named_location else ''

    context = {
        'alternative_distances': alternative_distances,
        'alternative_rome_descriptions': alternative_rome_descriptions,
        'related_romes': related_romes,
        'related_rome_initial': parameters.get('related_rome_initial', ''),
        'hide_suggestions': hide_suggestions,
        'canonical_url': canonical_url,
        'companies': list(offices),
        'companies_per_page': pagination.OFFICES_PER_PAGE,
        'company_count': office_count,
        'distance': fetcher.distance,
        'form': form,
        'headcount': fetcher.headcount,
        'job_doesnt_exist': False,
        'naf': fetcher.naf,
        'location': location,
        'departments': departments,
        'city_name': named_location.city if named_location else '',
        'location_name': named_location.name if named_location else '',
        'page': current_page,
        'pagination': pagination_manager,
        'rome_code': rome,
        'rome_description': settings.ROME_DESCRIPTIONS.get(rome, ''),
        'show_favorites': True,
        'sort': fetcher.sort,
        'tile_server_url': settings.TILE_SERVER_URL,
        'travel_mode': fetcher.travel_mode,
        'user_favs_as_sirets': UserFavoriteOffice.user_favs_as_sirets(current_user),
        'show_mobiville': pagination_manager.company_count <= settings.MOBIVILLE_MAX_COMPANY_COUNT and rome in settings.MOBIVILLE_ROMES,
    }

    activity_log_properties['distance'] = fetcher.distance
    activity_log_properties['effectif'] = fetcher.headcount
    activity_log_properties['tri'] = fetcher.sort
    activity_log_properties['naf'] = fetcher.naf
    activity_log_sirets = [office.siret for office in offices]
    activity.log_search(sirets=activity_log_sirets, count=office_count, page=current_page, **activity_log_properties)

    return render_template(template, **context)


def log_search_activity(activity_log_properties, offices=None, office_count=None, page=None):
    resultats = {
        'page': page,
        'total': office_count,
        'sirets': [office.siret for office in offices] if offices is not None else None,
    }
    activity.log('recherche', resultats=resultats, **activity_log_properties)


def get_canonical_results_url(zipcode, city, occupation):
    """
    The canonical url for each result page should have very few querystring arguments.
    """
    # Here we use urlencode instead of passing querystring values to url_for
    # because we want to preserve argument order.
    return url_for('search.entreprises') + '?' + urlencode([
        ('city', slugify(city)),
        ('zipcode', zipcode),
        ('occupation', slugify(occupation)),
    ])


def get_location(request_args: Dict[str, Union[str, int]]) -> Tuple[Optional[Location], Optional[NamedLocation], Optional[str]]:
    """
    Parse request parameters to extract a desired location and location names.

    Returns:

        location (Location) or None
        named_location (NamedLocation) or None
        departments (str)
    """

    # Parse location from latitude/longitude
    location = None
    named_location = None
    departments = None
    city_code = None
    if 'departments' in request_args:
        departments = request_args.get('departments')
        # FIXME: this is a temporary shortcut, we should use the department API to get the name and zipcode of the department
        zipcode = departments[0]
        location_name = city_name = request_args.get('l')
    else:
        city_code = zipcode = city_name = location_name = ''
        if 'lat' in request_args and 'lon' in request_args:
            try:
                latitude = float(request_args['lat'])
                longitude = float(request_args['lon'])
            except ValueError:
                pass
            else:
                location = Location(latitude, longitude)
                addresses = geocoding.get_address(latitude, longitude, limit=1)
                if addresses:
                    zipcode = addresses[0]['zipcode']
                    city_name = addresses[0]['city']
                    location_name = addresses[0]['label']
                    city_code = addresses[0]['city_code']

        # Parse location from zipcode/city slug (slug is optional)
        if location is None and 'zipcode' in request_args:
            zipcode = request_args['zipcode']
            city_slug = request_args.get('city', '')
            city = CityLocation(zipcode, city_slug)

            location = city.location
            zipcode = city.zipcode
            city_name = city.name
            location_name = city.full_name

        # Autocompletion has probably not worked: do autocompletion here
        if location is None and 'l' in request_args:
            try:
                result = geocoding.get_coordinates(request_args['l'], limit=1)[0]
            except IndexError:
                pass
            else:
                if 'latitude' in result and 'longitude' in result:
                    location = Location(result['latitude'], result['longitude'])
                    zipcode = result['zipcode']
                    city_name = result['city']
                    location_name = result['label']

    if zipcode and city_name and location_name:
        named_location = NamedLocation(zipcode, city_name, location_name, city_code)

    return location, named_location, departments


@searchBlueprint.route('/entreprises/commune/<commune_id>/rome/<rome_id>')
def results_by_commune_and_rome(commune_id, rome_id):
    """
    Convenience function to be used by Pôle Emploi, Bob Emploi and other partners
    Redirects internally to our real user-facing url displaying
    results for his search.

    The partners may use our API to get companies count, which comes with the URL to this route. @see labonneboite/web/api/views.py:compute_frontend_url

    For more information about the differences between commune_id and zipcode,
    please consult README file
    """
    fix_csrf_session()
    try:
        rome_description = settings.ROME_DESCRIPTIONS[rome_id.upper()]
        slugified_rome_description = slugify(rome_description)
    except KeyError:
        rome_description = None
    city = geocoding.get_city_by_commune_id(commune_id)

    if not city or not rome_description:
        abort(404)

    params = request.args.copy()
    params['city'] = city['slug']
    params['zipcode'] = city['zipcode']
    params['occupation'] = slugified_rome_description
    url = url_for('search.entreprises', **params)
    # Pass all GET params to the redirect URL: this will allow users of the API to build web links
    # roughly equivalent to the result of an API call - see Trello #971.
    return redirect(url)


@searchBlueprint.route('/entreprises/department/<department_code>/rome/<rome_id>')
def results_by_departments_and_rome(department_code, rome_id):
    """
    http://localhost:8080/entreprises/departments/57/rome/M1805
    Redirects internally to our real user-facing url displaying
    results for his search.

    The partners may use our API to get companies count, which comes with the URL to this route. @see labonneboite/web/api/views.py:compute_frontend_url
    """
    fix_csrf_session()
    url = get_url_for_rome(rome_id, department_code)
    if url is None:
        abort(400, 'Department or rome not found')
    return redirect(url)


def get_url_for_rome(rome_id, department_code):
    try:
        rome_description = settings.ROME_DESCRIPTIONS[rome_id.upper()]
        slugified_rome_description = slugify(rome_description)
    except KeyError:
        slugified_rome_description = None

    if slugified_rome_description:
        departmentData = geocoding.datagouv.get_department_by_code(department_code)
        if departmentData:
            return url_for('search.entreprises',
                           departments=department_code,
                           j=rome_id,
                           l=departmentData.get('label'),
                           occupation=slugified_rome_description)
    return None
