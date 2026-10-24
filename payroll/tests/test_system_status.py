from django.test import TestCase
from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import BaseTestContext
from core.test_helpers import LogInHelper, create_test_role
from payroll.schema import Mutation, Query

gql_payroll_system_status = """
query {
  payrollSystemStatus {
    triggersSynced
    message
  }
}
"""


class PayrollSystemStatusRightsTest(TestCase):
    """OP-3268: the payroll page checks the triggers before letting a user
    create a payroll, so a user who may create but not search payrolls needs
    the status too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gql_client = Client(Schema(query=Query, mutation=Mutation))

    def _status_for(self, username, perm_names):
        role = create_test_role(perm_names=perm_names, name=f"{username}Role")
        user = LogInHelper().get_or_create_user_api(username=username, roles=[role.id])
        return self.gql_client.execute(gql_payroll_system_status, context=BaseTestContext(user).get_request())

    def test_a_user_who_may_search_payrolls_gets_the_status(self):
        output = self._status_for("status_searcher", ["gql_payroll_search_perms"])

        self.assertIsNone(output.get("errors"), output)
        self.assertIn("triggersSynced", output["data"]["payrollSystemStatus"])

    def test_a_user_who_may_only_create_payrolls_gets_the_status(self):
        output = self._status_for("status_creator", ["gql_payroll_create_perms"])

        self.assertIsNone(output.get("errors"), output)
        self.assertIn("triggersSynced", output["data"]["payrollSystemStatus"])

    def test_a_user_without_payroll_rights_is_refused(self):
        output = self._status_for("status_outsider", [])

        self.assertIsNotNone(output.get("errors"))
        self.assertIsNone(output["data"]["payrollSystemStatus"])
