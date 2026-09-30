from unittest.mock import patch

from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import LogInHelper, create_test_role
from payroll.schema import Query, Mutation

GQL_PAYMENT_GATEWAY_CONFIG = """
query {
  paymentGatewayConfig {
    baseUrl
    apiKey
    timeout
  }
}
"""


@patch.multiple(
    "payroll.schema.PayrollConfig",
    gateway_base_url="http://gateway.test/",
    payment_gateway_api_key="test-api-key",
    payment_gateway_timeout=7,
)
class PaymentGatewayConfigGQLTestCase(openIMISGraphQLTestCase):
    """`paymentGatewayConfig` returns the gateway API key: only with its own right."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        authorized_role = create_test_role(
            perm_names=["gql_payment_gateway_config_query_perms"],
            name="PaymentGatewayConfigAuthorizedRole",
        )
        # Holds every other payroll right: running payrolls does not grant the secret.
        payroll_role = create_test_role(
            perm_names=[
                "gql_payroll_search_perms",
                "gql_payroll_create_perms",
                "gql_payroll_delete_perms",
                "gql_payment_point_search_perms",
            ],
            name="PaymentGatewayConfigPayrollRole",
        )
        user = LogInHelper().get_or_create_user_api(
            username="gw_cfg_allowed", roles=[authorized_role.id])
        payroll_user = LogInHelper().get_or_create_user_api(
            username="gw_cfg_payroll_only", roles=[payroll_role.id])
        cls.gql_client = Client(Schema(query=Query, mutation=Mutation))
        cls.context = BaseTestContext(user)
        cls.payroll_context = BaseTestContext(payroll_user)

    def test_returns_config_with_right(self):
        output = self.gql_client.execute(GQL_PAYMENT_GATEWAY_CONFIG, context=self.context.get_request())
        self.assertIsNone(output.get("errors"))
        self.assertEqual(output["data"]["paymentGatewayConfig"], {
            "baseUrl": "http://gateway.test/",
            "apiKey": "test-api-key",
            "timeout": 7,
        })

    def test_refused_without_right(self):
        output = self.gql_client.execute(GQL_PAYMENT_GATEWAY_CONFIG, context=self.payroll_context.get_request())
        self.assertTrue(output.get("errors"))
        self.assertIsNone((output.get("data") or {}).get("paymentGatewayConfig"))
