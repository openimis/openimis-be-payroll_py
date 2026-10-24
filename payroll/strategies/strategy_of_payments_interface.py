import abc
import logging

logger = logging.getLogger(__name__)


class StrategyOfPaymentInterface(object, metaclass=abc.ABCMeta):

    # True for a strategy whose agency pulls the payroll's payment list: the
    # rows show nothing of a pull, so the payroll as a whole may have been
    # paid from once it has been approved (``payroll_paid_reason``).
    LIST_PULLED_BY_AGENCY = False

    @classmethod
    def initialize_payment_gateway(cls, payment_point=None):
        pass

    @classmethod
    def accept_payroll(cls, payroll, user, **kwargs):
        pass

    @classmethod
    def make_payment_for_payroll(cls, payroll, user, **kwargs):
        pass

    @classmethod
    def reject_payroll(cls, payroll, user, **kwargs):
        from payroll.models import PayrollStatus
        cls.change_status_of_payroll(payroll, PayrollStatus.REJECTED, user)
        cls.remove_benefits_from_rejected_payroll(payroll)

    @classmethod
    def reject_approved_payroll(cls, payroll, user):
        from django.contrib.contenttypes.models import ContentType
        from core.services.utils.serviceUtils import model_representation
        from payroll.models import (
            BenefitConsumption,
            BenefitConsumptionStatus,
            PayrollStatus
        )
        from invoice.models import (
            DetailPaymentInvoice,
            PaymentInvoice,
            Bill
        )
        from payroll.services import PayrollService

        benefit_data = BenefitConsumption.objects.filter(
            payrollbenefitconsumption__payroll=payroll,
            status=BenefitConsumptionStatus.RECONCILED,
            is_deleted=False
        )
        benefit_data_related = list(benefit_data.values_list('id', 'benefitattachment__bill'))
        if len(benefit_data_related) > 0:
            benefits, related_bills = zip(*benefit_data_related)
            bill_content_type = ContentType.objects.get_for_model(Bill)
            detail_payment_invoices = DetailPaymentInvoice.objects.filter(
                subject_type=bill_content_type,
                subject_id__in=related_bills
            )
            payment_invoice_ids = list(detail_payment_invoices.values_list('payment_id', flat=True))
            detail_payment_invoices.delete()
            PaymentInvoice.objects.filter(id__in=payment_invoice_ids).delete()

        for benefit in benefit_data:
            benefit.receipt = None
            benefit.status = BenefitConsumptionStatus.ACCEPTED
            benefit.save(username=user.username)
        cls.change_status_of_payroll(payroll, PayrollStatus.PENDING_APPROVAL, user)
        PayrollService(user).create_accept_payroll_task(payroll.id, model_representation(payroll))

    @classmethod
    def acknowledge_of_reponse_view(cls, payroll, response_from_gateway, user, rejected_bills):
        pass

    @classmethod
    def reconcile_payroll(cls, payroll, user):
        pass

    @classmethod
    def change_status_of_payroll(cls, payroll, status, user):
        payroll.status = status
        payroll.save(username=user.login_name)

    @classmethod
    def sent_benefit_reasons(cls, benefits):
        """{benefit id: reason} for the benefits an agency may have paid:
        status APPROVE_FOR_PAYMENT or RECONCILED, or a receipt."""
        from payroll.models import BenefitConsumptionStatus
        sent_statuses = (BenefitConsumptionStatus.APPROVE_FOR_PAYMENT, BenefitConsumptionStatus.RECONCILED)
        reasons = {}
        for benefit in benefits:
            if benefit.status in sent_statuses:
                reasons[benefit.id] = f'status {benefit.status}'
            elif benefit.receipt:
                reasons[benefit.id] = 'receipt'
        return reasons

    @classmethod
    def payroll_paid_reason(cls, payroll):
        """Why an agency may have paid from the payroll as a whole, whatever
        its rows show, or None: the payroll of a strategy whose agency pulls
        its list (``LIST_PULLED_BY_AGENCY``) once it is APPROVE_FOR_PAYMENT
        or RECONCILED."""
        from payroll.models import PayrollStatus
        if cls.LIST_PULLED_BY_AGENCY and payroll.status in (
                PayrollStatus.APPROVE_FOR_PAYMENT, PayrollStatus.RECONCILED):
            return f'payroll {payroll.status}: its agency pulls its payment list'
        return None

    @classmethod
    def delete_payroll(cls, payroll, user, **kwargs):
        """Delete a payroll whose deletion task was approved; True when deleted.

        Runs in one transaction holding the payroll row and its benefit rows
        (``SELECT ... FOR UPDATE``). A payroll already deleted, one an agency
        may have paid from as a whole (``payroll_paid_reason``), or one holding
        a benefit an agency may have paid (``sent_benefit_reasons``, read on
        the locked rows) is not deleted: the refusal is logged and nothing
        changes. Otherwise its benefits are removed and the payroll is deleted.
        """
        from django.db import transaction
        from payroll.models import BenefitConsumption, Payroll, PayrollBenefitConsumption
        from payroll.services import PayrollService

        with transaction.atomic():
            payroll = Payroll.objects.select_for_update().get(id=payroll.id)
            if payroll.is_deleted:
                logger.error("Deletion of payroll %s refused: it is already deleted; nothing was done.",
                             payroll.id)
                return False
            paid = cls.payroll_paid_reason(payroll)
            if paid:
                logger.error("Deletion of payroll %s refused: %s; nothing was deleted.", payroll.id, paid)
                return False
            benefits = list(BenefitConsumption.objects.select_for_update().filter(
                id__in=PayrollBenefitConsumption.objects.filter(payroll=payroll).values('benefit_id'),
                is_deleted=False,
            ).order_by('id'))
            held = cls.sent_benefit_reasons(benefits)
            if held:
                logger.error(
                    "Deletion of payroll %s refused: %d benefit(s) may have been paid (%s); "
                    "nothing was deleted.",
                    payroll.id, len(held), ', '.join(sorted(set(held.values()))),
                )
                return False
            cls.remove_benefits_from_rejected_payroll(payroll)
            PayrollService(user).delete_instance(payroll)
        return True

    @classmethod
    def remove_benefits_from_rejected_payroll(cls, payroll):
        from payroll.models import (
            BenefitAttachment,
            BenefitConsumption,
            PayrollBenefitConsumption,
        )
        from invoice.models import (
            Bill,
            BillItem
        )

        benefit_data = BenefitConsumption.objects.filter(
            payrollbenefitconsumption__payroll=payroll,
            is_deleted=False
        ).values_list('id', 'benefitattachment__bill')

        if len(benefit_data) > 0:
            benefits, related_bills = zip(*benefit_data)

            BenefitAttachment.objects.filter(
                benefit_id__in=benefits
            ).delete()

            BillItem.objects.filter(
                bill__id__in=related_bills
            ).delete()

            Bill.objects.filter(
                id__in=related_bills
            ).delete()

            PayrollBenefitConsumption.objects.filter(payroll=payroll).delete()

            BenefitConsumption.objects.filter(
                id__in=benefits,
                is_deleted=False
            ).delete()

    @classmethod
    def remove_benefit_from_payroll(cls, benefit):
        from payroll.models import (
            BenefitAttachment,
            BenefitConsumption,
            PayrollBenefitConsumption
        )
        from invoice.models import (
            Bill,
            BillItem
        )

        benefit_data = BenefitConsumption.objects.filter(
            id=benefit.id,
            is_deleted=False
        ).values_list('id', 'benefitattachment__bill')

        if len(benefit_data) > 0:
            benefits, related_bills = zip(*benefit_data)

            BenefitAttachment.objects.filter(
                benefit_id__in=benefits
            ).delete()

            BillItem.objects.filter(
                bill__id__in=related_bills
            ).delete()

            Bill.objects.filter(
                id__in=related_bills
            ).delete()

            PayrollBenefitConsumption.objects.filter(benefit=benefit).delete()

            BenefitConsumption.objects.filter(
                id__in=benefits,
                is_deleted=False
            ).delete()
