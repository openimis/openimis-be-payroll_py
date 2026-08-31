import logging
from typing import Any, Dict, Optional

from core.models import User
from core.service_signals import ServiceSignalBindType
from core.signals import bind_service_signal
from openIMIS.openimisapps import openimis_apps
from tasks_management.models import Task
from payroll.apps import PayrollConfig
from payroll.models import Payroll, BenefitConsumption, BenefitConsumptionStatus
from payroll.payments_registry import PaymentMethodStorage
from payroll.services import PayrollService
from payroll.strategies import StrategyOfPaymentInterface

logger = logging.getLogger(__name__)
imis_modules = openimis_apps()  # Load openIMIS applications


def bind_service_signals():
    """
    Binds service signals to their respective event handlers. 
    These handlers manage various payroll-related tasks based on task completion events.
    """

    def fetch_task_user_result(kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extracts and returns task, user, and result data from the signal kwargs.
        Args:
            kwargs (Dict[str, Any]): Keyword arguments containing the task execution result.
        Returns:
            Optional[Dict[str, Any]]: Extracted task, user, and result information or None if extraction fails.
        """
        result = kwargs.get('result', {})
        if not result or not result.get('success'):
            return None

        try:
            task_data = result['data']['task']  # Get task data
            user = User.objects.get(id=result['data']['user']['id'])  # Fetch user from database
            return {
                "task": task_data,
                "user": user,
                "status": task_data['status'],
                "entity_id": task_data['entity_id'],
                "business_event": task_data['business_event'],
            }
        except (KeyError, User.DoesNotExist, ValueError) as exc:
            logger.error("Error extracting task or user", exc_info=exc)
            return None

    def handle_strategy_action(payroll: Payroll, user: User, action: str):
        """
        Calls the appropriate method on the payment strategy based on the action.
        Args:
            payroll (Payroll): The payroll object to act upon.
            user (User): The user performing the action.
            action (str): The name of the strategy method to call.
        """
        strategy = PaymentMethodStorage.get_chosen_payment_method(payroll.payment_method)
        if strategy:
            method = getattr(strategy, action, None)
            if callable(method):
                method(payroll, user)

    def on_task_complete_accept_payroll(**kwargs):
        """
        Handles the completion of a payroll acceptance task.
        """
        data = fetch_task_user_result(kwargs)
        if not data or data["business_event"] != PayrollConfig.payroll_accept_event:
            return

        payroll = Payroll.objects.get(id=data['entity_id'])  # Fetch payroll from database
        if data['status'] == Task.Status.COMPLETED:
            handle_strategy_action(payroll, data['user'], 'accept_payroll')
        elif data['status'] == Task.Status.FAILED:
            handle_strategy_action(payroll, data['user'], 'reject_payroll')

    def on_task_complete_payroll_reconciliation(**kwargs):
        """
        Handles the completion of a payroll reconciliation task.
        """
        data = fetch_task_user_result(kwargs)
        if not data or data["business_event"] != PayrollConfig.payroll_reconciliation_event:
            return

        if data['status'] == Task.Status.COMPLETED:
            payroll = Payroll.objects.get(id=data['entity_id'])
            handle_strategy_action(payroll, data['user'], 'reconcile_payroll')

    def on_task_complete_payroll_reject_approved_payroll(**kwargs):
        """
        Handles the rejection of an approved payroll.
        """
        data = fetch_task_user_result(kwargs)
        if not data or data["business_event"] != PayrollConfig.payroll_reject_event:
            return

        if data['status'] == Task.Status.COMPLETED:
            payroll = Payroll.objects.get(id=data['entity_id'])
            handle_strategy_action(payroll, data['user'], 'reject_approved_payroll')

    def on_task_delete_payroll(**kwargs):
        """
        Handles the deletion of a payroll record after task completion.
        """
        data = fetch_task_user_result(kwargs)
        if not data or data["business_event"] != PayrollConfig.payroll_delete_event:
            return

        if data['status'] == Task.Status.COMPLETED:
            payroll = Payroll.objects.get(id=data['entity_id'])
            strategy = PaymentMethodStorage.get_chosen_payment_method(payroll.payment_method)
            if strategy:
                strategy.remove_benefits_from_rejected_payroll(payroll)  # Remove benefits if necessary
            PayrollService(data['user']).delete_instance(payroll)  # Delete the payroll

    def on_task_delete_benefit(**kwargs):
        """
        Handles the deletion of a benefit after task completion.
        """
        data = fetch_task_user_result(kwargs)
        if not data or data["business_event"] != PayrollConfig.benefit_delete_event:
            return

        benefit = BenefitConsumption.objects.get(id=data['entity_id'])  # Get the benefit object
        if data['status'] == Task.Status.COMPLETED:
            StrategyOfPaymentInterface.remove_benefit_from_payroll(benefit)  # Remove benefit
        elif data['status'] == Task.Status.FAILED:
            benefit.status = BenefitConsumptionStatus.ACCEPTED  # Mark as accepted if failed
            benefit.save(username=data['user'].username)

    # Bind the signals to respective event handlers
    bind_service_signal('task_service.complete_task', on_task_complete_accept_payroll, ServiceSignalBindType.AFTER)
    bind_service_signal('task_service.complete_task', on_task_complete_payroll_reconciliation, ServiceSignalBindType.AFTER)
    bind_service_signal('task_service.complete_task', on_task_complete_payroll_reject_approved_payroll, ServiceSignalBindType.AFTER)
    bind_service_signal('task_service.complete_task', on_task_delete_payroll, ServiceSignalBindType.AFTER)
    bind_service_signal('task_service.complete_task', on_task_delete_benefit, ServiceSignalBindType.AFTER)
