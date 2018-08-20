# coding: utf8
from labonneboite.common.models import Office
from labonneboite.tests.test_base import DatabaseTest


class RouteTest(DatabaseTest):

    def create_example_office(self):
        # Create an office.
        self.office = Office(
            departement='75',
            siret='78548035101646',
            company_name='NICOLAS',
            headcount='03',
            city_code='75110',
            zipcode='75010',
            naf='7320Z',
            tel='0100000000',
            score=80,
            x=2.3488,
            y=48.8534,
        )
        self.office.save()

    def test_office_fields_and_properties_are_unicode(self):
        """
        Check if office fields are unicode
        """

        self.create_example_office()

        self.assertEqual(type(self.office.company_name), str)
        self.assertEqual(type(self.office.address_as_text), str)
        self.assertEqual(type(self.office.phone), str)
        self.assertEqual(type(self.office.google_url), str)

    def test_office_details_page(self):
        """
        Test the office details page of a regular office.
        """

        self.create_example_office()

        rv = self.app.get(self.url_for('office.details', siret=self.office.siret))
        self.assertEqual(rv.status_code, 200)

    def test_office_details_page_of_non_existing_office(self):
        """
        Test the office details page of a non existing office.
        """

        # The details page of an nonexistent office should raise a 404.
        rv = self.app.get(self.url_for('office.details', siret='7x5x8x3x1x1x46'))
        self.assertEqual(rv.status_code, 404)

    def test_office_details_page_of_office_having_buggy_naf(self):
        """
        Test the office details page of an office having NAF 9900Z.
        """

        self.create_example_office()
        self.office.naf = '9900Z'
        self.office.save()

        rv = self.app.get(self.url_for('office.details', siret=self.office.siret))
        self.assertEqual(rv.status_code, 200)

    def test_download_regular_office(self):
        """
        Test the office PDF download
        """

        self.create_example_office()

        # normal behavior
        rv = self.app.get(self.url_for('office.download', siret=self.office.siret))

        self.assertEqual(rv.status_code, 200)
        self.assertEqual('application/pdf', rv.mimetype)
        self.assertLess(1000, rv.content_length)

    def test_download_missing_siret(self):
        """
        Test the office PDF download of a non existing office
        """
        # siret does not exist
        rv = self.app.get(self.url_for('office.download', siret='1234567890'))
        self.assertEqual(rv.status_code, 404)

    def test_download_of_office_having_buggy_naf(self):
        """
        Test the office PDF download of an office having NAF 9900Z.
        """

        self.create_example_office()
        self.office.naf = '9900Z'
        self.office.save()

        rv = self.app.get(self.url_for('office.download', siret=self.office.siret))
        self.assertEqual(rv.status_code, 200)
