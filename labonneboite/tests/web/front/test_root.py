from unittest import mock

from labonneboite.tests.test_base import DatabaseTest
from labonneboite.common import pro
from labonneboite.common.models import User


class RootBaseTest(DatabaseTest):
    def setUp(self):
        super(RootBaseTest, self).setUp()

        # Create a user.
        self.pro_user = User.create(email='x@pole-emploi.fr',
                                    gender='male',
                                    first_name='John',
                                    last_name='Doe')


class RootTest(RootBaseTest):

    def test_no_kit_if_public_user(self):
        rv = self.app.get(self.url_for('root.kit'))
        self.assertEqual(rv.status_code, 404)

    @mock.patch('labonneboite.conf.settings.VERSION_PRO_ALLOWED_EMAIL_SUFFIXES', ['@pole-emploi.fr'])
    def test_no_kit_if_pro_but_not_enabled(self):
        with self.login_client.test_client(user=self.pro_user) as client:

            rv = client.get(self.url_for('root.kit'))
            self.assertEqual(rv.status_code, 404)

    @mock.patch('labonneboite.conf.settings.VERSION_PRO_ALLOWED_EMAIL_SUFFIXES', ['@pole-emploi.fr'])
    def test_kit_if_pro_and_enabled(self):
        with self.login_client.test_client(user=self.pro_user) as client:

            # enable pro version
            with client.session_transaction() as sess:
                sess[pro.PRO_VERSION_SESSION_KEY] = True

            # Non-empty kit page
            rv = client.get(self.url_for('root.kit'))
            self.assertEqual(rv.status_code, 200)
            self.assertEqual('text/html; charset=utf-8', rv.content_type)
            self.assertLess(1000, rv.content_length)
