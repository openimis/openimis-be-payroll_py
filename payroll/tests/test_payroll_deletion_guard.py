"""A payroll is not deleted while an agency may have paid from it (OP-3267).

Ported from the deletion part of be-payroll #92 (release/26.04,
``test_payment_guards.py``):

- The deletion request is refused for a deleted payroll, for an approved
  payroll whose agency pulls its list, and, once the payroll is past the
  statuses no agency has been asked to pay in, for a payroll holding a
  benefit that may have been paid.
- The approved deletion task checks again under lock and changes nothing
  when the payroll holds such a benefit or was deleted meanwhile.
"""
import uuid
from datetime import date
from unittest import mock

from django.test import TestCase

from core.test_helpers import LogInHelper
from individual.models import Individual
from payroll.apps import PayrollConfig
from payroll.models import (
    BenefitConsumption, BenefitConsumptionStatus, Payroll, PayrollBenefitConsumption,
    PayrollStatus,
)
from payroll.services import PayrollService
from payroll.strategies import StrategyOfflinePayment, StrategyOnlinePayment


class _RowsFixtures(TestCase):
    """Payrolls whose benefits each carry a bill, a bill item and an attachment.

    Reads go through ``objects.all()``: core's manager answers get() and filter()
    on an id from a cache that a queryset update or delete does not invalidate.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = LogInHelper().get_or_create_user_api(username='payroll_guard_user')
        cls.individual = Individual(first_name='Guard', last_name='Payment', dob='1990-01-01')
        cls.individual.save(username=cls.user.username)

    def _payroll(self, status):
        payroll = Payroll(name=f'P-{uuid.uuid4().hex[:6]}', status=status,
                          payment_method='StrategyGuardTest', json_ext={})
        payroll.save(username=self.user.username)
        return payroll

    def _benefit(self, status, payroll):
        benefit = BenefitConsumption(
            individual=self.individual, code=f'BEN-{uuid.uuid4().hex[:8]}',
            amount=72000, type='Cash Transfer', status=status, date_due=date(2026, 10, 1),
            json_ext={})
        benefit.save(username=self.user.username)
        PayrollBenefitConsumption(payroll=payroll, benefit=benefit).save(username=self.user.username)
        return benefit

    def _billed(self, status, payroll, receipt=None):
        from invoice.models import Bill, BillItem
        from payroll.models import BenefitAttachment

        benefit = self._benefit(status, payroll)
        if receipt:
            BenefitConsumption.objects.all().filter(id=benefit.id).update(receipt=receipt)
        bill = Bill(code=f'B-{uuid.uuid4().hex[:8]}', amount_total=72000, amount_net=72000)
        bill.save(username=self.user.username)
        BillItem(bill=bill, code=f'BI-{uuid.uuid4().hex[:8]}', amount_total=72000).save(
            username=self.user.username)
        BenefitAttachment(benefit=benefit, bill=bill).save(username=self.user.username)
        return BenefitConsumption.objects.all().get(id=benefit.id), bill

    def _rows(self, payroll):
        self.reconciled, _ = self._billed(BenefitConsumptionStatus.RECONCILED, payroll, receipt='RCPT-1')
        self.sent, _ = self._billed(BenefitConsumptionStatus.APPROVE_FOR_PAYMENT, payroll)
        self.receipted, _ = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll, receipt='IBB-2')
        self.waiting, _ = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)

    def _deletion_tasks(self, payroll):
        from tasks_management.models import Task
        return Task.objects.filter(entity_id=str(payroll.id),
                                   business_event=PayrollConfig.payroll_delete_event)

    def assertRemoved(self, benefit, bill):
        from invoice.models import Bill, BillItem
        from payroll.models import BenefitAttachment
        self.assertFalse(BenefitConsumption.objects.all().filter(id=benefit.id).exists())
        self.assertFalse(PayrollBenefitConsumption.objects.all().filter(benefit_id=benefit.id).exists())
        self.assertFalse(BenefitAttachment.objects.all().filter(benefit_id=benefit.id).exists())
        self.assertFalse(Bill.objects.all().filter(id=bill.id).exists())
        self.assertFalse(BillItem.objects.all().filter(bill_id=bill.id).exists())

    def assertUntouched(self, payroll, benefits):
        self.assertFalse(Payroll.objects.all().get(id=payroll.id).is_deleted)
        for benefit in benefits:
            row = BenefitConsumption.objects.all().get(id=benefit.id)
            self.assertEqual((row.is_deleted, row.status), (False, benefit.status))
            self.assertTrue(PayrollBenefitConsumption.objects.all().filter(
                payroll=payroll, benefit_id=benefit.id, is_deleted=False).exists())


class _TestPullStrategy(StrategyOnlinePayment):
    """An online strategy whose agency pulls its list: no gateway."""
    LIST_PULLED_BY_AGENCY = True

    @classmethod
    def initialize_payment_gateway(cls, payment_point=None):
        cls.PAYMENT_GATEWAY = None


class PayrollDeletionTaskTest(_RowsFixtures):
    """The approved deletion task deletes a payroll nothing was paid from, and
    nothing else."""

    def _delete_through_task(self, payroll, status_at_completion=None):
        """Request the deletion, then complete its task; with
        ``status_at_completion``, the payroll reaches that status between the
        request and the completion."""
        from tasks_management.models import Task
        from tasks_management.services import TaskService

        PayrollService(self.user).delete({'id': payroll.id})
        task = Task.objects.get(entity_id=str(payroll.id),
                                business_event=PayrollConfig.payroll_delete_event)
        if status_at_completion:
            Payroll.objects.all().filter(id=payroll.id).update(status=status_at_completion)
        with mock.patch('payroll.signals.PaymentMethodStorage.get_chosen_payment_method',
                        return_value=StrategyOfflinePayment):
            TaskService(self.user).complete_task({'id': task.id})

    def test_a_payroll_nothing_was_paid_from_is_deleted(self):
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        waiting, bill = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)

        self._delete_through_task(payroll)

        self.assertTrue(Payroll.objects.all().get(id=payroll.id).is_deleted)
        self.assertRemoved(waiting, bill)

    def test_a_payroll_paid_after_the_request_is_not_deleted(self):
        # Requested while PENDING_APPROVAL, approved and paid before the task completes.
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        self._rows(payroll)

        with self.assertLogs('payroll.strategies', level='ERROR'):
            self._delete_through_task(payroll, status_at_completion=PayrollStatus.APPROVE_FOR_PAYMENT)

        self.assertUntouched(payroll, (self.reconciled, self.sent, self.receipted, self.waiting))

    def test_a_benefit_found_paid_under_lock_stops_the_deletion(self):
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        waiting, _ = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
        other, _ = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)

        def reasons(cls, benefits):
            return {benefit.id: 'push attempt' for benefit in benefits if benefit.id == waiting.id}

        with mock.patch.object(StrategyOfflinePayment, 'sent_benefit_reasons', classmethod(reasons)), \
                self.assertLogs('payroll.strategies', level='ERROR'):
            deleted = StrategyOfflinePayment.delete_payroll(payroll, self.user)

        self.assertFalse(deleted)
        self.assertUntouched(payroll, (waiting, other))

    def test_an_approved_pulled_payroll_is_not_deleted(self):
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        waiting, _ = self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
        Payroll.objects.all().filter(id=payroll.id).update(status=PayrollStatus.APPROVE_FOR_PAYMENT)

        with self.assertLogs('payroll.strategies', level='ERROR'):
            deleted = _TestPullStrategy.delete_payroll(Payroll.objects.all().get(id=payroll.id), self.user)

        self.assertFalse(deleted)
        self.assertUntouched(payroll, (waiting,))

    def test_a_second_approved_deletion_changes_nothing(self):
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
        self.assertTrue(StrategyOfflinePayment.delete_payroll(payroll, self.user))
        versions = Payroll.history.filter(id=payroll.id).count()

        with self.assertLogs('payroll.strategies', level='ERROR'):
            self.assertFalse(StrategyOfflinePayment.delete_payroll(payroll, self.user))
        self.assertEqual(Payroll.history.filter(id=payroll.id).count(), versions)


class PayrollDeletionRequestTest(_RowsFixtures):
    """A payroll is put up for deletion only while nothing can have been paid from it."""

    def test_a_deleted_payroll_is_not_put_up_for_deletion(self):
        payroll = self._payroll(PayrollStatus.PENDING_APPROVAL)
        Payroll.objects.all().filter(id=payroll.id).update(is_deleted=True)
        with self.assertRaises(ValueError):
            PayrollService(self.user).delete({'id': payroll.id})
        self.assertFalse(self._deletion_tasks(payroll).exists())

    def test_a_payroll_holding_a_sent_benefit_is_refused_once_approved(self):
        for status in (PayrollStatus.APPROVE_FOR_PAYMENT, PayrollStatus.RECONCILED,
                       PayrollStatus.REJECTED):
            with self.subTest(payroll_status=status):
                payroll = self._payroll(status)
                self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
                self._billed(BenefitConsumptionStatus.APPROVE_FOR_PAYMENT, payroll)
                with self.assertRaises(ValueError):
                    PayrollService(self.user).delete({'id': payroll.id})
                self.assertFalse(self._deletion_tasks(payroll).exists())

    def test_an_approved_payroll_whose_agency_pulls_its_list_is_refused(self):
        """The rows of a pulled payroll show nothing of the pull: once approved,
        it is refused even when none of its rows shows a payment."""
        for status in (PayrollStatus.APPROVE_FOR_PAYMENT, PayrollStatus.RECONCILED):
            with self.subTest(payroll_status=status):
                payroll = self._payroll(status)
                self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
                with mock.patch('payroll.payments_registry.PaymentMethodStorage.get_chosen_payment_method',
                                return_value=_TestPullStrategy), \
                        self.assertRaises(ValueError):
                    PayrollService(self.user).delete({'id': payroll.id})
                self.assertFalse(self._deletion_tasks(payroll).exists())

    @mock.patch.object(PayrollConfig, 'pre_approval_payroll_statuses', ['PENDING_VERIFICATION'])
    def test_a_payroll_not_yet_approved_is_put_up_for_deletion(self):
        for status in (PayrollStatus.PENDING_APPROVAL, 'PENDING_VERIFICATION',
                       PayrollStatus.GENERATING, PayrollStatus.FAILED):
            with self.subTest(payroll_status=status):
                payroll = self._payroll(status)
                self._billed(BenefitConsumptionStatus.ACCEPTED, payroll)
                PayrollService(self.user).delete({'id': payroll.id})
                self.assertEqual(self._deletion_tasks(payroll).count(), 1)

    @mock.patch.object(PayrollConfig, 'pre_approval_payroll_statuses', [])
    def test_an_undeclared_status_is_checked_before_deletion(self):
        payroll = self._payroll('PENDING_VERIFICATION')
        self._billed(BenefitConsumptionStatus.APPROVE_FOR_PAYMENT, payroll)
        with self.assertRaises(ValueError):
            PayrollService(self.user).delete({'id': payroll.id})
        self.assertFalse(self._deletion_tasks(payroll).exists())
