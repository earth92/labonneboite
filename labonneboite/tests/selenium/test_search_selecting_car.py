"""
Test search using isochrone this filter: car.
Data used in these tests are generated by this SQL script:
docker/alembic/etablissements_tests_selenium.
"""

import time
import re
import urllib.parse

from parameterized import parameterized
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from labonneboite.common.maps.constants import ISOCHRONE_DURATIONS_MINUTES
from .base import LbbSeleniumTestCase


DURATIONS = [(str(duration), ) for duration in ISOCHRONE_DURATIONS_MINUTES]


class TestSearchSelectingCar(LbbSeleniumTestCase):

    def setUp(self):
        super().setUp()
        url = self.url_for(
            'search.entreprises',
            l='Metz+57050',
            occupation='comptabilite',
            tr='car',
            lat='49.119146',
            lon='6.176026',
        )
        self.driver.get(url)

        # Wait a little bit more to ensure things are less flaky
        time.sleep(3)


    @parameterized.expand(DURATIONS)
    def test_isochrone_search(self, duration):
        """
        Test an isochrone search selecting "car" and "{duration} minutes".
        """

        # Store current results
        results_sentence = self.driver.find_element_by_css_selector('h1.lbb-result-info').text
        primitive_results = re.match(r'(\d+)', results_sentence).group()

        # Click on "min"
        self.driver.find_element_by_css_selector('.switch-element[data-switch-value="duration"]')\
            .click()

        # Travel modes should be visible now
        car_button = self.driver.find_element_by_css_selector(
            '.travelmode-choices a.visible[data-travelmode="car"]'
        )
        self.assertTrue(car_button.is_displayed())

        # Filtering by duration should be available now
        durations_options = self.driver.find_element_by_css_selector('#isochrone-durations')
        self.assertTrue(durations_options.is_displayed())

        # click on another duration
        durations_options.find_element_by_css_selector(f'input[value="{duration}"]').click()

        # The page should reload with a new search.
        time.sleep(1)

        # Assert the selected duration and its mode are part of the url.
        current_url = self.driver.current_url
        url = urllib.parse.urlparse(current_url)
        parameters = dict(urllib.parse.parse_qsl(url.query))

        self.assertEqual(parameters['tr'], 'car')
        self.assertEqual(parameters['dur'], str(duration))

        results_sentence = self.driver.find_element_by_css_selector('h1.lbb-result-info').text
        last_results = re.match(r'(\d+)', results_sentence).group()

        expected_results = {
            '15': '10',
            '30': '13',
            '45': '16',
        }

        self.assertEqual(last_results, expected_results[duration])
        self.assertGreaterEqual(int(last_results), int(primitive_results))


    def test_commute_time_is_displayed(self):
        """
        Each office details should have a commute time
        displayed along with other information.
        As default transport mode is car, it's not needed to select it again.
        """

        # Find the first element that matches this CSS selector.
        enterprise_details = self.driver.find_element_by_css_selector('.lbb-result')
        travel_duration_text = enterprise_details.find_element_by_css_selector('.travel-duration').text

        # Make sure duration is displayed.
        self.assertRegex(travel_duration_text, r'(\d+)')

        # Make sure travel mode is displayed
        self.assertIn('voiture', travel_duration_text)
