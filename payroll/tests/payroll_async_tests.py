import uuid
from unittest import mock

from django.test import override_settings
from payroll.models import Payroll, PayrollStatus, PayrollBenefitConsumption
from payroll.services import PayrollService
from payroll.tasks import create_payroll_benefits_task
from contribution_plan.tests.helpers import create_test_payment_plan
from payroll.tests.payroll_gql_tests import PayrollGQLTestCase
from payroll.tests.data import gql_payroll_create, gql_payroll_retrigger


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class PayrollAsyncTests(PayrollGQLTestCase):

    def test_create_payroll_async_success(self):
        """Verify that creating a payroll triggers the async task and transitions
        from GENERATING to PENDING_APPROVAL on success."""
        name = "AsyncPayrollTest"
        Payroll.objects.filter(name=name).delete()

        variables = {
            "name": name,
            "paymentCycleId": str(self.payment_cycle.id),
            "paymentPlanId": str(self.payment_plan.id),
            "paymentPointId": str(self.payment_point.id),
            "paymentMethod": self.payment_method,
            "dateValidFrom": self.date_valid_from,
            "dateValidTo": self.date_valid_to,
            "jsonExt": self.json_ext_able_bodied_true,
            "clientMutationId": str(uuid.uuid4())
        }

        # the task is queued once the payroll is committed (OP-3255)
        with self.captureOnCommitCallbacks(execute=True):
            output = self.gql_client.execute(
                gql_payroll_create,
                context=self.gql_context.get_request(),
                variable_values=variables
            )
        self.assertIsNone(output.get('errors'), f"Mutation errors: {output.get('errors')}")

        payroll = Payroll.objects.get(name=name)
        self.assertEqual(payroll.status, PayrollStatus.PENDING_APPROVAL)

        benefit_count = PayrollBenefitConsumption.objects.filter(payroll=payroll).count()
        self.assertGreater(benefit_count, 0, "No benefits were created by the async task")

    def test_retrigger_payroll_creation(self):
        """Verify that retriggering a failed payroll restarts the creation process."""
        name = "RetriggerPayrollTest"
        Payroll.objects.filter(name=name).delete()

        creation_params = {
            "name": name,
            "payment_cycle_id": str(self.payment_cycle.id),
            "payment_plan_id": str(self.payment_plan.id),
            "payment_point_id": str(self.payment_point.id),
            "payment_method": self.payment_method,
            "date_valid_from": self.date_valid_from,
            "date_valid_to": self.date_valid_to,
            "json_ext": self.json_ext_able_bodied_true,
        }

        payroll = Payroll(
            name=name,
            payment_cycle=self.payment_cycle,
            payment_plan=self.payment_plan,
            payment_point=self.payment_point,
            payment_method=self.payment_method,
            status=PayrollStatus.FAILED,
            date_valid_from=self.date_valid_from,
            date_valid_to=self.date_valid_to,
            json_ext={
                "creation_params": creation_params,
                "creation_error": "Some old error"
            }
        )
        payroll.save(username='username_authorized')

        variables = {"id": str(payroll.id)}
        with self.captureOnCommitCallbacks(execute=True):
            output = self.gql_client.execute(
                gql_payroll_retrigger,
                context=self.gql_context.get_request(),
                variable_values=variables
            )
        self.assertIsNone(output.get('errors'), f"Retrigger errors: {output.get('errors')}")

        payroll.refresh_from_db()
        self.assertEqual(payroll.status, PayrollStatus.PENDING_APPROVAL)
        self.assertNotIn('creation_error', payroll.json_ext or {})

        benefit_count = PayrollBenefitConsumption.objects.filter(payroll=payroll).count()
        self.assertGreater(benefit_count, 0, "No benefits were created after retriggering")

    def test_create_payroll_persists_failed_status_on_bad_plan(self):
        """Verify that when benefit generation fails, the payroll row persists
        with FAILED status and creation_error/creation_params in json_ext."""
        name = "FailedCreationTest"
        Payroll.objects.filter(name=name).delete()

        service = PayrollService(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            service.create({
                "name": name,
                "payment_plan_id": str(self.payment_plan.id),
                "payment_point_id": str(self.payment_point.id),
                "payment_method": self.payment_method,
                "date_valid_from": self.date_valid_from,
                "date_valid_to": self.date_valid_to,
            })

        payroll = Payroll.objects.filter(name=name, is_deleted=False).first()
        self.assertIsNotNone(payroll, "Payroll row must persist even after failed benefit generation")
        self.assertEqual(payroll.status, PayrollStatus.FAILED)
        self.assertIn('creation_error', payroll.json_ext or {})
        self.assertIn('creation_params', payroll.json_ext or {})

    def test_retrigger_without_creation_params_returns_error(self):
        """Verify retrigger_creation returns an error when creation_params is absent."""
        name = "NoParamsRetriggerTest"
        Payroll.objects.filter(name=name).delete()

        payroll = Payroll(
            name=name,
            payment_plan=self.payment_plan,
            payment_cycle=self.payment_cycle,
            payment_point=self.payment_point,
            payment_method=self.payment_method,
            status=PayrollStatus.FAILED,
            date_valid_from=self.date_valid_from,
            date_valid_to=self.date_valid_to,
            json_ext={"creation_error": "original error"}  # No creation_params
        )
        payroll.save(username='username_authorized')

        service = PayrollService(self.user)
        result = service.retrigger_creation({"id": payroll.id})

        self.assertFalse(result.get("success", True), "Expected retrigger to return failure")
        payroll.refresh_from_db()
        self.assertEqual(payroll.status, PayrollStatus.FAILED, "Status must stay FAILED")

    def test_retrigger_non_failed_payroll_returns_error(self):
        """Verify retrigger_creation rejects payrolls that are not in FAILED status."""
        name = "RetriggerNonFailedTest"
        Payroll.objects.filter(name=name).delete()

        payroll = Payroll(
            name=name,
            payment_plan=self.payment_plan,
            payment_cycle=self.payment_cycle,
            payment_point=self.payment_point,
            payment_method=self.payment_method,
            status=PayrollStatus.PENDING_APPROVAL,
            date_valid_from=self.date_valid_from,
            date_valid_to=self.date_valid_to,
            json_ext={"creation_params": {"some": "params"}}
        )
        payroll.save(username='username_authorized')

        service = PayrollService(self.user)
        result = service.retrigger_creation({"id": payroll.id})

        self.assertFalse(result.get("success", True), "Expected retrigger to be rejected for non-FAILED payroll")
        payroll.refresh_from_db()
        self.assertEqual(payroll.status, PayrollStatus.PENDING_APPROVAL, "Status must not change")

    # OP-3255: benefit generation was queued inside the mutation's transaction, so a
    # free worker could look the payroll up before it was committed, fail with
    # DoesNotExist and leave it GENERATING for good.

    def _create_variables(self, name):
        Payroll.objects.filter(name=name).delete()
        return {
            "name": name,
            "paymentCycleId": str(self.payment_cycle.id),
            "paymentPlanId": str(self.payment_plan.id),
            "paymentPointId": str(self.payment_point.id),
            "paymentMethod": self.payment_method,
            "dateValidFrom": self.date_valid_from,
            "dateValidTo": self.date_valid_to,
            "jsonExt": self.json_ext_able_bodied_true,
            "clientMutationId": str(uuid.uuid4())
        }

    def test_benefit_generation_is_queued_once_the_payroll_is_committed(self):
        name = "AfterCommitPayrollTest"
        with mock.patch("payroll.services.create_payroll_benefits_task.delay") as delay:
            with self.captureOnCommitCallbacks() as callbacks:
                output = self.gql_client.execute(
                    gql_payroll_create,
                    context=self.gql_context.get_request(),
                    variable_values=self._create_variables(name),
                )
                self.assertIsNone(output.get('errors'), f"Mutation errors: {output.get('errors')}")
                self.assertEqual(delay.call_count, 0, "queued before the transaction committed")
            for callback in callbacks:
                callback()

        payroll = Payroll.objects.get(name=name)
        delay.assert_called_once()
        self.assertEqual(delay.call_args.args[0], str(payroll.id))

    def test_generation_error_in_the_worker_marks_the_payroll_failed(self):
        name = "WorkerFailurePayrollTest"
        Payroll.objects.filter(name=name).delete()
        payroll = Payroll(
            name=name,
            payment_plan=self.payment_plan,
            payment_cycle=self.payment_cycle,
            payment_point=self.payment_point,
            payment_method=self.payment_method,
            status=PayrollStatus.GENERATING,
            date_valid_from=self.date_valid_from,
            date_valid_to=self.date_valid_to,
            json_ext={"creation_params": {"name": name}},
        )
        payroll.save(username='username_authorized')

        with mock.patch.object(PayrollService, "_create_payroll_benefits", side_effect=ValueError("no beneficiaries")):
            with self.assertRaises(ValueError):
                create_payroll_benefits_task(str(payroll.id), str(self.user.id), {})

        payroll.refresh_from_db()
        self.assertEqual(payroll.status, PayrollStatus.FAILED, "a failed generation must be retriggerable")
        self.assertIn("no beneficiaries", payroll.json_ext.get("creation_error", ""))
        self.assertIn("creation_params", payroll.json_ext)
    def test_create_payroll_on_a_product_payment_plan_is_refused(self):
        """OP-3263: a payment plan linked to an insurance product has no programme to
        draw beneficiaries from; the payroll used to be saved, then fail generation
        with "'Product' object has no attribute 'type'"."""
        name = "ProductPlanPayrollTest"
        Payroll.objects.filter(name=name).delete()
        product_plan = create_test_payment_plan()

        result = PayrollService(self.user).create({
            "name": name,
            "payment_plan_id": str(product_plan.id),
            "payment_point_id": str(self.payment_point.id),
            "payment_method": self.payment_method,
            "date_valid_from": self.date_valid_from,
            "date_valid_to": self.date_valid_to,
        })

        self.assertFalse(result.get("success", True), result)
        self.assertIn(
            "payroll.create.payment_plan_without_programme",
            str(result.get("detail", "")) + str(result.get("message", "")),
        )
        self.assertFalse(Payroll.objects.filter(name=name).exists(), "nothing may be saved")
