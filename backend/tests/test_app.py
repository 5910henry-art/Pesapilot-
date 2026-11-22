import unittest
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import event
from cryptography.fernet import Fernet

from app import create_app
from app.extensions import db
from app.models.loan_repayments import LoanPaymentAllocation
from app.models import (
    BusinessProfile,
    Currency,
    AuditLog,
    User,
    UserProfile,
    RefreshToken,
    Workspace,
    WorkspaceMember,
    FinancialAccount,
    LedgerAccount,
    JournalEntry,
    JournalLine,
    SavingsGoal,
    ExpenseCategory,
    Budget,
    PersonalLoan,
    LoanInstallment,
    LoanPayment,
    LoanDisbursement,
    Bill,
    BillPayment,
    PlatformAdmin,
    PlatformAccountingSettings,
    PlatformFinancialAccount,
    PlatformLedgerAccount,
    PlatformJournalEntry,
    PlatformJournalLine,
    PlatformLoanProduct,
    PlatformLoanApplication,
    PlatformLoanApplicationDetails,
    PlatformLoanAffordabilityAssessment,
    PlatformLoan,
    PlatformLoanInstallment,
    PlatformLoanDisbursement,
    PlatformLoanPayment,
    PlatformLoanPaymentAllocation,
    PlatformLoanLateFee,
    PlatformLoanLateFeePayment,
    PlatformLoanLateFeePaymentAllocation,
    PlatformInvestor,
    PlatformInvestorContribution,
    PlatformInvestorWithdrawal,
    PlatformLoanFundingAllocation,
    PlatformLoanFundingRelease,
)

from app.services.accounting import create_draft_entry, post_entry, reverse_entry
from app.services.identity import decrypt_national_id
from app.services.platform_lending import (
    PlatformLendingValidationError,
    create_platform_loan_from_application,
    disburse_platform_loan,
    record_platform_loan_payment,
    review_loan_application,
    confirm_platform_loan_payment,
    reject_platform_loan_payment,
    reverse_platform_loan_payment,
    assess_overdue_platform_loan_fees,
    record_platform_late_fee_payment,
    confirm_platform_late_fee_payment,
    reject_platform_late_fee_payment,
    reverse_platform_late_fee_payment,
    record_platform_investor_contribution,
    record_platform_investor_withdrawal,
    reverse_platform_investor_contribution,
    reverse_platform_investor_withdrawal,
)
from app.services.platform_accounting import (
    PlatformAccountingError,
    create_draft_entry as create_platform_draft_entry,
    post_entry as post_platform_entry,
    reverse_entry as reverse_platform_entry,
)
from app.services.ai_manager.data_service import build_financial_snapshot
from app.services.ai_manager.rules_engine import generate_insights
from app.services.ai_manager.providers.groq import sanitize_financial_context
from app.services.loan_schedules import build_repayment_schedule
from app.services.platform_affordability_evidence import (
    build_affordability_evidence_report,
    collect_existing_monthly_debt,
)
from app.services.loan_repayments import (
    build_payment_allocations,
    confirm_loan_payment,
)
from flask_jwt_extended import create_access_token, create_refresh_token
from werkzeug.security import generate_password_hash, check_password_hash


class PesaPilotTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "isolated-test-secret-key-32-bytes-minimum",
            "JWT_SECRET_KEY": "isolated-test-jwt-secret-key-32-bytes-minimum",
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
            "IDENTITY_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        })

        cls.app_context = cls.app.app_context()
        cls.app_context.push()

        if db.engine.dialect.name == "sqlite":
            event.listen(
                db.engine,
                "connect",
                lambda connection, record: connection.execute(
                    "PRAGMA foreign_keys=ON"
                ),
            )

        db.create_all()

        db.session.add(Currency(
            code="KES",
            name="Kenyan Shilling",
            minor_unit=2,
            is_active=True,
        ))
        db.session.commit()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        db.session.remove()
        db.engine.dispose()
        cls.app_context.pop()

    def setUp(self):
        # Give every test a clean database, including records committed
        # by earlier tests in this shared in-memory SQLite database.
        db.session.remove()
        db.drop_all()
        db.create_all()

        db.session.add(Currency(
            code="KES",
            name="Kenyan Shilling",
            minor_unit=2,
            is_active=True,
        ))
        db.session.commit()

        self.client = self.app.test_client()

        self.user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(self.user)
        db.session.flush()

        self.workspace = Workspace(
            name="Test Workspace",
            workspace_type="personal",
            functional_currency="KES",
        )
        db.session.add(self.workspace)
        db.session.flush()

        self.membership = WorkspaceMember(
            workspace_id=self.workspace.id,
            user_id=self.user.id,
            role="owner",
        )
        db.session.add(self.membership)

        self.source_account = FinancialAccount(
            workspace_id=self.workspace.id,
            name="Main Wallet",
            account_type="mobile_money",
            currency_code="KES",
            is_active=True,
        )
        db.session.add(self.source_account)
        db.session.flush()

        self.source_ledger = LedgerAccount(
            workspace_id=self.workspace.id,
            financial_account_id=self.source_account.id,
            code=f"SRC-{uuid4().hex[:12]}",
            name="Main Wallet",
            account_class="asset",
            normal_balance="debit",
            currency_code="KES",
            is_system=False,
            is_active=True,
        )

        self.equity_ledger = LedgerAccount(
            workspace_id=self.workspace.id,
            financial_account_id=None,
            code=f"EQ-{uuid4().hex[:12]}",
            name="Opening Balance Equity",
            account_class="equity",
            normal_balance="credit",
            currency_code="KES",
            is_system=True,
            is_active=True,
        )

        self.customer_liability_ledger = LedgerAccount(
            workspace_id=self.workspace.id,
            financial_account_id=None,
            code="2000",
            name="Loans and Credit Payable",
            account_class="liability",
            normal_balance="credit",
            currency_code="KES",
            is_system=True,
            is_active=True,
        )

        db.session.add_all([
            self.source_ledger,
            self.equity_ledger,
            self.customer_liability_ledger,
        ])
        db.session.flush()

        # Seed the funding account with KES 1,000.00.
        opening = create_draft_entry(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            reference=f"TEST-OPEN-{uuid4().hex}",
            description="Test opening balance",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": self.source_ledger.id,
                    "debit_minor": 100000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": self.equity_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": 100000,
                },
            ],
        )

        post_entry(
            entry_id=opening.id,
            workspace_id=self.workspace.id,
            posted_by=self.user.id,
        )
        db.session.commit()

        self.token = create_access_token(
            identity=str(self.user.id)
        )
        self.headers = {
            "Authorization": f"Bearer {self.token}",
        }

    def tearDown(self):
        db.session.rollback()
        db.session.remove()

    def test_inactive_user_access_token_is_rejected_immediately(self):
        url = f"/api/v1/loans?workspace_id={self.workspace.id}"

        active_response = self.client.get(
            url,
            headers=self.headers,
        )
        self.assertEqual(
            active_response.status_code,
            200,
            active_response.json,
        )

        self.user.is_active = False
        db.session.commit()

        inactive_response = self.client.get(
            url,
            headers=self.headers,
        )
        self.assertEqual(
            inactive_response.status_code,
            401,
            inactive_response.json,
        )

    def test_account_deletion_eligibility_allows_clean_account(self):
        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertTrue(response.json["eligible"])
        self.assertEqual(response.json["blockers"], [])

    def test_account_deletion_eligibility_requires_authentication(self):
        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility"
        )
        self.assertEqual(response.status_code, 401)

    def test_account_deletion_is_blocked_by_outstanding_platform_loan(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=self.user.id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=50000,
            outstanding_principal_minor=50000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="active",
        )
        db.session.add(loan)
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("outstanding_platform_loan", codes)
        self.assertFalse(response.json["eligible"])

    def test_account_deletion_is_blocked_by_pending_platform_application(self):
        product = self.create_platform_loan_product()
        db.session.add(PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Test application",
            status="pending",
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("pending_platform_application", codes)
        self.assertFalse(response.json["eligible"])

    def test_account_deletion_is_blocked_when_user_owns_shared_workspace(self):
        workspace = Workspace(
            name="Shared Business",
            workspace_type="business",
            functional_currency="KES",
        )
        db.session.add(workspace)
        db.session.flush()

        other_user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(other_user)
        db.session.flush()

        db.session.add_all([
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=self.user.id,
                role="owner",
            ),
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=other_user.id,
                role="member",
            ),
        ])
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        blockers = response.json["blockers"]
        ownership = next(
            item for item in blockers
            if item["code"] == "shared_workspace_ownership"
        )
        self.assertEqual(
            ownership["workspaces"][0]["workspace_id"],
            str(workspace.id),
        )
        self.assertFalse(response.json["eligible"])

    def test_account_deletion_is_blocked_when_user_owns_shared_personal_workspace(self):
        other_user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(other_user)
        db.session.flush()

        db.session.add(WorkspaceMember(
            workspace_id=self.workspace.id,
            user_id=other_user.id,
            role="member",
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        ownership = next(
            item for item in response.json["blockers"]
            if item["code"] == "shared_workspace_ownership"
        )
        self.assertEqual(
            ownership["workspaces"][0]["workspace_id"],
            str(self.workspace.id),
        )
        self.assertFalse(response.json["eligible"])

    def test_account_deletion_is_blocked_for_last_active_platform_admin(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("last_platform_admin", codes)
        self.assertFalse(response.json["eligible"])

    def test_account_deletion_allows_fully_paid_platform_loan(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=self.user.id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=100000,
            outstanding_principal_minor=0,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="paid",
        )
        db.session.add(loan)
        db.session.commit()

        response = self.client.get(
            "/api/v1/auth/account-deletion/eligibility",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["eligible"], response.json)
        self.assertEqual(response.json["blockers"], [])

    def test_account_deletion_endpoint_requires_authentication(self):
        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            json={"password": "test-password", "confirmation": "DELETE"},
        )
        self.assertEqual(response.status_code, 401)

    def test_account_deletion_endpoint_requires_exact_confirmation(self):
        self.user.password_hash = generate_password_hash("test-password")
        db.session.commit()

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "test-password", "confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertIsNotNone(db.session.get(User, self.user.id))

    def test_account_deletion_endpoint_rejects_wrong_password(self):
        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "wrong-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 401, response.json)
        self.assertIsNotNone(db.session.get(User, self.user.id))

    def test_account_deletion_endpoint_permanently_deletes_clean_account(self):
        user_id = self.user.id
        workspace_id = self.workspace.id

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["deleted_workspace_count"], 1)
        self.assertEqual(response.json["preserved_shared_workspace_count"], 0)
        self.assertIsNone(db.session.get(User, user_id))
        self.assertIsNone(db.session.get(Workspace, workspace_id))

    def test_account_deletion_removes_populated_personal_workspace(self):
        user_id = self.user.id
        workspace_id = self.workspace.id

        # A personal workspace should be removed when it has no other
        # members, even if its membership role is unexpectedly not owner.
        self.membership.role = "member"

        today = date.today()
        loan = PersonalLoan(
            workspace_id=workspace_id,
            created_by=user_id,
            direction="borrowed",
            counterparty_name="Deletion Test Counterparty",
            loan_type="interest_free",
            principal_minor=50000,
            currency_code="KES",
            interest_method="none",
            interest_rate_percent=Decimal("0"),
            repayment_frequency="one_time",
            term_count=1,
            start_date=today,
            due_date=today + timedelta(days=30),
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        installment = LoanInstallment(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
            installment_number=1,
            due_date=loan.due_date,
            amount_minor=50000,
            principal_minor=50000,
            interest_minor=0,
            paid_minor=0,
            status="scheduled",
        )
        goal = SavingsGoal(
            workspace_id=workspace_id,
            name="Deletion Test Savings Goal",
            target_amount_minor=100000,
            currency_code="KES",
            financial_account_id=self.source_account.id,
            created_by=user_id,
        )
        db.session.add_all([installment, goal])
        db.session.commit()

        loan_id = loan.id
        installment_id = installment.id
        goal_id = goal.id
        account_id = self.source_account.id
        opening_journal_id = JournalEntry.query.filter_by(
            workspace_id=workspace_id
        ).first().id

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["deleted_workspace_count"], 1)
        self.assertIsNone(db.session.get(User, user_id))
        self.assertIsNone(db.session.get(Workspace, workspace_id))
        self.assertIsNone(db.session.get(PersonalLoan, loan_id))
        self.assertIsNone(db.session.get(LoanInstallment, installment_id))
        self.assertIsNone(db.session.get(SavingsGoal, goal_id))
        self.assertIsNone(db.session.get(FinancialAccount, account_id))
        self.assertIsNone(db.session.get(JournalEntry, opening_journal_id))

    def test_account_deletion_preserves_shared_workspace_and_other_members(self):
        user_id = self.user.id
        personal_workspace_id = self.workspace.id

        other_user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(other_user)
        db.session.flush()

        shared_workspace = Workspace(
            name="Shared Team Workspace",
            workspace_type="business",
            functional_currency="KES",
        )
        db.session.add(shared_workspace)
        db.session.flush()

        shared_account = FinancialAccount(
            workspace_id=shared_workspace.id,
            name="Shared Business Wallet",
            account_type="mobile_money",
            currency_code="KES",
            is_active=True,
        )
        db.session.add_all([
            shared_account,
            WorkspaceMember(
                workspace_id=shared_workspace.id,
                user_id=other_user.id,
                role="owner",
            ),
            WorkspaceMember(
                workspace_id=shared_workspace.id,
                user_id=user_id,
                role="member",
            ),
        ])

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        shared_workspace_id = shared_workspace.id
        shared_account_id = shared_account.id
        other_user_id = other_user.id

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["deleted_workspace_count"], 1)
        self.assertEqual(response.json["preserved_shared_workspace_count"], 1)

        self.assertIsNone(db.session.get(User, user_id))
        self.assertIsNone(db.session.get(Workspace, personal_workspace_id))
        self.assertIsNotNone(db.session.get(Workspace, shared_workspace_id))
        self.assertIsNotNone(db.session.get(FinancialAccount, shared_account_id))

        self.assertIsNone(WorkspaceMember.query.filter_by(
            workspace_id=shared_workspace_id,
            user_id=user_id,
        ).first())

        remaining_membership = WorkspaceMember.query.filter_by(
            workspace_id=shared_workspace_id,
            user_id=other_user_id,
        ).first()
        self.assertIsNotNone(remaining_membership)
        self.assertEqual(remaining_membership.role, "owner")

    def test_account_deletion_anonymises_counterparty_in_retained_loan(self):
        user_id = self.user.id

        other_user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(other_user)
        db.session.flush()

        shared_workspace = Workspace(
            name="Counterparty Retention Workspace",
            workspace_type="business",
            functional_currency="KES",
        )
        db.session.add(shared_workspace)
        db.session.flush()

        db.session.add_all([
            WorkspaceMember(
                workspace_id=shared_workspace.id,
                user_id=other_user.id,
                role="owner",
            ),
            WorkspaceMember(
                workspace_id=shared_workspace.id,
                user_id=user_id,
                role="member",
            ),
        ])

        loan = PersonalLoan(
            workspace_id=shared_workspace.id,
            created_by=other_user.id,
            direction="lent",
            counterparty_user_id=user_id,
            counterparty_name="Account Being Deleted",
            counterparty_phone="+254700000000",
            loan_type="interest_free",
            principal_minor=75000,
            currency_code="KES",
            interest_method="none",
            interest_rate_percent=Decimal("0"),
            repayment_frequency="monthly",
            term_count=3,
            installment_minor=25000,
            start_date=date.today(),
            due_date=date.today() + timedelta(days=90),
            status="active",
            description="Private counterparty details",
        )
        db.session.add(loan)

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        loan_id = loan.id
        workspace_id = shared_workspace.id
        other_user_id = other_user.id

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(db.session.get(User, user_id))

        preserved_loan = db.session.get(PersonalLoan, loan_id)
        self.assertIsNotNone(preserved_loan)
        self.assertEqual(preserved_loan.workspace_id, workspace_id)
        self.assertEqual(preserved_loan.created_by, other_user_id)
        self.assertEqual(preserved_loan.principal_minor, 75000)
        self.assertEqual(preserved_loan.installment_minor, 25000)
        self.assertEqual(preserved_loan.status, "active")
        self.assertIsNone(preserved_loan.counterparty_user_id)
        self.assertEqual(preserved_loan.counterparty_name, "Deleted user")
        self.assertIsNone(preserved_loan.counterparty_phone)
        self.assertIsNone(preserved_loan.description)

        self.assertIsNotNone(db.session.get(Workspace, workspace_id))
        self.assertIsNotNone(db.session.get(User, other_user_id))

    def test_account_deletion_endpoint_blocks_outstanding_platform_loan(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=self.user.id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=50000,
            outstanding_principal_minor=50000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="active",
        )
        db.session.add(loan)
        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        user_id = self.user.id
        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 409, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("outstanding_platform_loan", codes)
        self.assertIsNotNone(db.session.get(User, user_id))

    def test_account_deletion_endpoint_blocks_pending_platform_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Test application",
            status="pending",
        )
        db.session.add(application)
        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        user_id = self.user.id
        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 409, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("pending_platform_application", codes)
        self.assertIsNotNone(db.session.get(User, user_id))

    def test_account_deletion_endpoint_blocks_pending_platform_payment(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=self.user.id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=100000,
            outstanding_principal_minor=100000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        db.session.add(PlatformLoanPayment(
            loan_id=loan.id,
            currency_code="KES",
            amount_minor=10000,
            principal_minor=0,
            interest_minor=0,
            payment_date=date.today(),
            payment_method="mobile_money",
            status="pending",
            recorded_by=self.user.id,
            notes="Pending customer repayment",
        ))

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        user_id = self.user.id
        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 409, response.json)
        codes = {item["code"] for item in response.json["blockers"]}
        self.assertIn("pending_platform_payment", codes)
        self.assertIsNotNone(db.session.get(User, user_id))

    def test_account_deletion_redacts_rejected_platform_payment_history(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=self.user.id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=100000,
            outstanding_principal_minor=0,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="paid",
        )
        db.session.add(loan)
        db.session.flush()

        payment = PlatformLoanPayment(
            loan_id=loan.id,
            currency_code="KES",
            amount_minor=10000,
            principal_minor=0,
            interest_minor=0,
            payment_date=date.today(),
            payment_method="mobile_money",
            status="rejected",
            recorded_by=self.user.id,
            rejected_by=self.user.id,
            rejected_at=datetime.now(timezone.utc),
            rejection_reason="Personal information in original rejection",
            notes="Customer-specific private notes",
        )
        db.session.add(payment)

        self.user.password_hash = generate_password_hash("correct-password")
        db.session.commit()

        user_id = self.user.id
        loan_id = loan.id
        payment_id = payment.id

        response = self.client.delete(
            "/api/v1/auth/account-deletion",
            headers=self.headers,
            json={"password": "correct-password", "confirmation": "DELETE"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertIsNone(db.session.get(User, user_id))

        retained_loan = db.session.get(PlatformLoan, loan_id)
        self.assertIsNotNone(retained_loan)
        self.assertIsNone(retained_loan.customer_id)
        self.assertEqual(retained_loan.outstanding_principal_minor, 0)

        retained_payment = db.session.get(PlatformLoanPayment, payment_id)
        self.assertIsNotNone(retained_payment)
        self.assertEqual(retained_payment.amount_minor, 10000)
        self.assertEqual(retained_payment.status, "rejected")
        self.assertIsNone(retained_payment.recorded_by)
        self.assertIsNone(retained_payment.rejected_by)
        self.assertIsNone(retained_payment.notes)
        self.assertEqual(
            retained_payment.rejection_reason,
            "Redacted after account deletion",
        )

    def test_active_currency_catalogue_requires_authentication(self):
        response = self.client.get("/api/v1/auth/currencies")
        self.assertEqual(response.status_code, 401)

    def test_active_currency_catalogue_returns_only_active_currencies(self):
        currency = Currency.query.filter_by(code="XXX").first()
        created_currency = currency is None
        original_active = None

        if currency is None:
            currency = Currency(
                code="XXX",
                name="Inactive Currency",
                minor_unit=2,
                is_active=False,
            )
            db.session.add(currency)
        else:
            original_active = currency.is_active
            currency.is_active = False

        db.session.commit()

        try:
            response = self.client.get(
                "/api/v1/auth/currencies",
                headers=self.headers,
            )

            self.assertEqual(response.status_code, 200, response.json)
            codes = [
                item["code"]
                for item in response.json["currencies"]
            ]
            self.assertEqual(codes, ["KES"])
            self.assertNotIn("XXX", codes)
        finally:
            if created_currency:
                db.session.delete(currency)
            else:
                currency.is_active = original_active

            db.session.commit()

    def test_profile_update_changes_details_and_records_audit(self):
        profile = UserProfile(
            user_id=self.user.id,
            first_name="Henry",
            last_name="Mushiyu",
            timezone="Africa/Nairobi",
            preferred_currency="KES",
        )
        db.session.add(profile)
        self.user.phone = "+254700000001"
        self.user.phone_verified = True
        db.session.commit()

        response = self.client.patch(
            "/api/v1/auth/profile",
            headers=self.headers,
            json={
                "first_name": "Henry James",
                "last_name": "Mushiyu",
                "phone": "+254712345678",
                "preferred_currency": "KES",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["profile"]["first_name"], "Henry James")
        self.assertEqual(response.json["user"]["phone"], "+254712345678")
        self.assertFalse(response.json["user"]["phone_verified"])

        audit = AuditLog.query.filter_by(
            user_id=self.user.id,
            action="profile_updated",
        ).one()

        self.assertIn("first_name", audit.details["changed_fields"])
        self.assertIn("phone", audit.details["changed_fields"])
        self.assertTrue(audit.details["phone_verification_reset"])

    def test_profile_update_rejects_blank_name(self):
        db.session.add(UserProfile(
            user_id=self.user.id,
            first_name="Henry",
            last_name="Mushiyu",
            timezone="Africa/Nairobi",
            preferred_currency="KES",
        ))
        db.session.commit()

        response = self.client.patch(
            "/api/v1/auth/profile",
            headers=self.headers,
            json={"first_name": "   "},
        )

        self.assertEqual(response.status_code, 400)

    def test_profile_update_rejects_inactive_currency(self):
        db.session.add(UserProfile(
            user_id=self.user.id,
            first_name="Henry",
            last_name="Mushiyu",
            timezone="Africa/Nairobi",
            preferred_currency="KES",
        ))

        currency = Currency.query.filter_by(code="XXX").first()
        created_currency = currency is None
        original_active = None

        if currency is None:
            currency = Currency(
                code="XXX",
                name="Inactive Currency",
                minor_unit=2,
                is_active=False,
            )
            db.session.add(currency)
        else:
            original_active = currency.is_active
            currency.is_active = False

        db.session.commit()

        try:
            response = self.client.patch(
                "/api/v1/auth/profile",
                headers=self.headers,
                json={"preferred_currency": "XXX"},
            )

            self.assertEqual(response.status_code, 400)
        finally:
            if created_currency:
                db.session.delete(currency)
            else:
                currency.is_active = original_active

            db.session.commit()

    def test_business_workspace_registration_creates_complete_workspace(self):
        payload = {
            "name": "Acme Traders",
            "business_type": "Retail",
            "country": "Kenya",
            "currency": "KES",
            "registration_number": "PVT-12345",
            "business_phone": "+254712345678",
            "business_email": "accounts@acme.example",
            "address": "Nairobi",
            "user_id": str(uuid4()),
        }

        response = self.client.post(
            "/api/v1/auth/workspaces/business",
            headers=self.headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        workspace_data = response.json["workspace"]
        workspace_id = UUID(workspace_data["id"])

        self.assertEqual(workspace_data["name"], "Acme Traders")
        self.assertEqual(workspace_data["type"], "business")
        self.assertEqual(workspace_data["currency"], "KES")
        self.assertEqual(workspace_data["role"], "owner")

        workspace = db.session.get(Workspace, workspace_id)
        self.assertIsNotNone(workspace)
        self.assertEqual(workspace.workspace_type, "business")

        profile = db.session.get(BusinessProfile, workspace_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.business_type, "Retail")
        self.assertEqual(profile.country, "Kenya")
        self.assertEqual(profile.registration_number, "PVT-12345")
        self.assertEqual(profile.business_phone, "+254712345678")
        self.assertEqual(profile.business_email, "accounts@acme.example")
        self.assertEqual(profile.address, "Nairobi")

        membership = WorkspaceMember.query.filter_by(
            workspace_id=workspace_id
        ).one()
        self.assertEqual(membership.user_id, self.user.id)
        self.assertEqual(membership.role, "owner")

        ledger_count = LedgerAccount.query.filter_by(
            workspace_id=workspace_id
        ).count()
        self.assertEqual(ledger_count, 10)

        audit = AuditLog.query.filter_by(
            workspace_id=workspace_id,
            action="business_workspace_created",
        ).one()
        self.assertEqual(audit.user_id, self.user.id)

        current_user = self.client.get(
            "/api/v1/auth/me",
            headers=self.headers,
        )
        self.assertEqual(current_user.status_code, 200)
        workspace_ids = {
            item["id"] for item in current_user.json["workspaces"]
        }
        self.assertIn(str(self.workspace.id), workspace_ids)
        self.assertIn(str(workspace_id), workspace_ids)

    def test_business_workspace_registration_allows_multiple_workspaces(self):
        for name in ("First Business", "Second Business"):
            response = self.client.post(
                "/api/v1/auth/workspaces/business",
                headers=self.headers,
                json={
                    "name": name,
                    "business_type": "Retail",
                    "country": "Kenya",
                },
            )
            self.assertEqual(response.status_code, 201, response.json)

        business_count = (
            db.session.query(WorkspaceMember)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .filter(
                WorkspaceMember.user_id == self.user.id,
                Workspace.workspace_type == "business",
            )
            .count()
        )
        self.assertEqual(business_count, 2)

    def test_business_workspace_registration_rejects_invalid_payloads(self):
        valid_payload = {
            "name": "Valid Business",
            "business_type": "Retail",
            "country": "Kenya",
            "currency": "KES",
        }

        invalid_payloads = [
            {"name": ""},
            {"name": 123},
            {"business_type": ""},
            {"country": ""},
            {"currency": "INVALID"},
            {"business_email": "not-an-email"},
            {"business_phone": 12345},
            {"registration_number": "X" * 101},
        ]

        for invalid_fields in invalid_payloads:
            with self.subTest(invalid_fields=invalid_fields):
                payload = {**valid_payload, **invalid_fields}
                response = self.client.post(
                    "/api/v1/auth/workspaces/business",
                    headers=self.headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 400, response.json)

    def test_business_workspace_registration_requires_authentication(self):
        response = self.client.post(
            "/api/v1/auth/workspaces/business",
            json={
                "name": "Unauthorized Business",
                "business_type": "Retail",
                "country": "Kenya",
            },
        )
        self.assertEqual(response.status_code, 401)


    def test_no_consent_still_counts_recorded_borrowed_personal_debt(self):
        created = self.create_personal_loan()
        self.assertEqual(created.status_code, 201, created.json)

        loan = db.session.get(PersonalLoan, UUID(created.json["loan"]["id"]))
        loan.status = "active"
        db.session.commit()

        report = build_affordability_evidence_report(
            customer_id=self.user.id,
            declared_monthly_income_minor=4_000_000,
            declared_monthly_expenses_minor=2_000_000,
            declared_existing_monthly_repayments_minor=0,
            consent=False,
        )

        self.assertEqual(
            report["debt_evidence"]["existing_personal_monthly_repayments_minor"],
            9167,
        )
        self.assertEqual(
            report["effective_existing_monthly_repayments_minor"],
            9167,
        )
        self.assertEqual(
            report["workspace_evidence"]["months_with_records"],
            0,
        )

    def test_lent_personal_loan_is_not_counted_as_customer_debt(self):
        created = self.create_personal_loan(direction="lent")
        self.assertEqual(created.status_code, 201, created.json)

        loan = db.session.get(PersonalLoan, UUID(created.json["loan"]["id"]))
        loan.status = "active"
        db.session.commit()

        debt = collect_existing_monthly_debt(customer_id=self.user.id)

        self.assertEqual(
            debt["existing_personal_monthly_repayments_minor"],
            0,
        )

    def create_personal_loan(self, **overrides):
        start = date.today()
        payload = {
            "workspace_id": str(self.workspace.id),
            "direction": "borrowed",
            "counterparty_name": "Jane Doe",
            "counterparty_phone": "0712345678",
            "loan_type": "personal",
            "principal_minor": 100000,
            "currency_code": "KES",
            "interest_method": "flat",
            "interest_rate_percent": "10",
            "repayment_frequency": "monthly",
            "term_count": 12,
            "installment_minor": 9167,
            "start_date": start.isoformat(),
            "due_date": (start + timedelta(days=30)).isoformat(),
            "description": "Personal loan test",
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/loans",
            headers=self.headers,
            json=payload,
        )

    def create_goal(self, name="Emergency Fund", target=50000):
        response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": name,
                "target_amount_minor": target,
            },
        )
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["goal"]

    def transfer(self, goal_id, operation, amount):
        return self.client.post(
            f"/api/v1/savings/{goal_id}/{operation}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "source_account_id": str(self.source_account.id),
                "amount_minor": amount,
            },
        )

    def source_balance_minor(self):
        lines = (
            JournalLine.query
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .filter(
                JournalLine.ledger_account_id == self.source_ledger.id,
                JournalEntry.workspace_id == self.workspace.id,
                JournalEntry.status.in_(("posted", "reversed")),
            )
            .all()
        )
        return sum(
            line.functional_debit_minor - line.functional_credit_minor
            for line in lines
        )

    def create_category(self, name="Food"):
        return self.client.post(
            "/api/v1/budgets/categories",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": name,
            },
        )

    def test_create_expense_category(self):
        response = self.create_category("Food")

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        category = response.json["category"]
        self.assertEqual(category["name"], "Food")
        self.assertTrue(category["is_active"])
        self.assertIsNotNone(category["ledger_account"])
        self.assertEqual(
            category["ledger_account"]["name"],
            "Expenses - Food",
        )

        ledger = LedgerAccount.query.filter_by(
            id=UUID(category["ledger_account"]["id"]),
            workspace_id=self.workspace.id,
        ).one()

        self.assertEqual(ledger.account_class, "expense")
        self.assertEqual(ledger.normal_balance, "debit")
        self.assertEqual(ledger.currency_code, "KES")



    def platform_application_payload(self, product, **overrides):
        payload = {
            "product_id": str(product.id),
            "customer_financial_account_id": str(self.source_account.id),
            "requested_principal_minor": 100000,
            "term_count": 6,
            "purpose": "Business stock",
            "full_name": "Test Applicant",
            "national_id": "12345678",
            "phone": "+254700000000",
            "email": self.user.email,
            "residential_address": "Nairobi, Kenya",
            "employment_status": "employed",
            "employer_business_name": "Test Employer",
            "monthly_income_minor": 4000000,
            "monthly_expenses_minor": 2000000,
            "existing_monthly_repayments_minor": 0,
            "explanation": None,
            "alternative_contact_name": None,
            "alternative_contact_phone": None,
            "alternative_contact_relationship": None,
            "additional_notes": None,
            "affordability_consent": True,
        }
        payload.update(overrides)
        return payload

    def create_verified_income_evidence(
        self, amount_minor=600000, transaction_date=None
    ):
        return self.create_income_entry(
            amount_minor, transaction_date or datetime.now(timezone.utc)
        )

    def create_platform_loan_product(self, **overrides):
        values = {
            "name": "Test Personal Loan",
            "code": f"TEST-PL-{uuid4().hex[:12]}",
            "description": "Test platform lending product",
            "currency_code": "KES",
            "min_principal_minor": 50000,
            "max_principal_minor": 5000000,
            "interest_method": "reducing",
            "interest_rate_percent": Decimal("10"),
            "repayment_frequency": "monthly",
            "min_term_count": 1,
            "max_term_count": 12,
            "origination_fee_minor": 0,
            "late_payment_fee_minor": 0,
            "allow_early_repayment": True,
            "is_active": True,
            "created_by": self.user.id,
        }
        values.update(overrides)
        product = PlatformLoanProduct(**values)
        db.session.add(product)
        db.session.flush()
        return product

    def create_approved_platform_application(
        self,
        *,
        requested_principal_minor=100000,
        approved_principal_minor=None,
        term_count=6,
        product=None,
    ):
        product = product or self.create_platform_loan_product()
        approved_amount = (
            approved_principal_minor
            if approved_principal_minor is not None
            else requested_principal_minor
        )
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=requested_principal_minor,
            approved_principal_minor=approved_amount,
            term_count=term_count,
            purpose="Business stock",
            status="approved",
            reviewed_by=self.user.id,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.session.add(application)
        db.session.flush()
        db.session.add(PlatformLoanAffordabilityAssessment(
            application_id=application.id,
            version=1,
            status="verified",
            estimated_affordable_principal_minor=approved_amount,
            recommended_monthly_instalment_minor=0,
            report={},
            reasons=[],
        ))
        db.session.flush()
        return application


    def create_platform_accounting_setup(self):
        if db.session.get(PlatformAccountingSettings, 1) is None:
            db.session.add(PlatformAccountingSettings(
                id=1,
                functional_currency_code="KES",
                updated_by=self.user.id,
            ))

        financial_account = PlatformFinancialAccount(
            name="PesaPilot Test Wallet",
            account_type="mobile_money",
            currency_code="KES",
            is_active=True,
        )
        db.session.add(financial_account)
        db.session.flush()

        self.platform_cash_ledger = PlatformLedgerAccount(
            financial_account_id=financial_account.id,
            code=f"PL-CASH-{uuid4().hex[:12]}",
            name="Platform Test Wallet",
            account_class="asset",
            normal_balance="debit",
            currency_code="KES",
            is_system=False,
            is_active=True,
        )
        self.platform_equity_ledger = PlatformLedgerAccount(
            financial_account_id=None,
            code=f"PL-EQ-{uuid4().hex[:12]}",
            name="Platform Test Equity",
            account_class="equity",
            normal_balance="credit",
            currency_code="KES",
            is_system=True,
            is_active=True,
        )
        self.platform_interest_income_ledger = (
            PlatformLedgerAccount.query.filter_by(
                code="PL-LOANS-INTEREST-INCOME-KES",
                currency_code="KES",
            ).one_or_none()
        )

        if self.platform_interest_income_ledger is None:
            self.platform_interest_income_ledger = PlatformLedgerAccount(
                financial_account_id=None,
                code="PL-LOANS-INTEREST-INCOME-KES",
                name="Platform Loans Interest Income",
                account_class="income",
                normal_balance="credit",
                currency_code="KES",
                is_system=True,
                is_active=True,
            )

        self.platform_receivable_ledger = (
            PlatformLedgerAccount.query.filter_by(
                code="PL-LOANS-RECEIVABLE-KES",
                currency_code="KES",
            ).one_or_none()
        )

        if self.platform_receivable_ledger is None:
            self.platform_receivable_ledger = PlatformLedgerAccount(
                financial_account_id=None,
                code="PL-LOANS-RECEIVABLE-KES",
                name="Platform Loans Receivable",
                account_class="asset",
                normal_balance="debit",
                currency_code="KES",
                is_system=True,
                is_active=True,
            )
        self.platform_investor_capital_ledger = (
            PlatformLedgerAccount.query.filter_by(
                code="PL-INVESTOR-CAPITAL-KES",
                currency_code="KES",
            ).one_or_none()
        )

        if self.platform_investor_capital_ledger is None:
            self.platform_investor_capital_ledger = PlatformLedgerAccount(
                financial_account_id=None,
                code="PL-INVESTOR-CAPITAL-KES",
                name="External Investor Capital",
                account_class="liability",
                normal_balance="credit",
                currency_code="KES",
                is_system=True,
                is_active=True,
            )

        db.session.add_all([
            self.platform_cash_ledger,
            self.platform_equity_ledger,
            self.platform_receivable_ledger,
            self.platform_interest_income_ledger,
            self.platform_investor_capital_ledger,
        ])
        db.session.flush()

        return financial_account

    def create_platform_late_fee_ledgers(self):
        definitions = (
            (
                "PL-LOANS-LF-REC-KES",
                "Platform Loan Late Fee Receivable",
                "asset",
                "debit",
            ),
            (
                "PL-LOANS-LF-INC-KES",
                "Platform Loan Late Fee Income",
                "income",
                "credit",
            ),
        )
        for code, name, account_class, normal_balance in definitions:
            ledger = PlatformLedgerAccount.query.filter_by(
                code=code,
                currency_code="KES",
            ).one_or_none()
            if ledger is None:
                db.session.add(PlatformLedgerAccount(
                    financial_account_id=None,
                    code=code,
                    name=name,
                    account_class=account_class,
                    normal_balance=normal_balance,
                    currency_code="KES",
                    is_system=True,
                    is_active=True,
                ))
        db.session.flush()

    def create_platform_investor_with_capital(
        self, financial_account, amount_minor=150000
    ):
        investor = PlatformInvestor(
            name=f"Test Investor {uuid4().hex[:8]}",
            created_by=self.user.id,
            is_active=True,
        )
        db.session.add(investor)
        db.session.flush()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-INV-CONTRIB-{uuid4().hex[:12]}",
            description="Test investor capital contribution",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": self.platform_cash_ledger.id,
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": self.platform_investor_capital_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                },
            ],
        )
        post_platform_entry(entry_id=entry.id, posted_by=self.user.id)

        contribution = PlatformInvestorContribution(
            investor_id=investor.id,
            financial_account_id=financial_account.id,
            journal_entry_id=entry.id,
            currency_code="KES",
            amount_minor=amount_minor,
            contribution_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
            status="posted",
        )
        db.session.add(contribution)
        db.session.flush()

        return investor, contribution

    def test_platform_loan_service_creates_loan_from_approved_application(self):
        application = self.create_approved_platform_application(
            requested_principal_minor=100000,
            approved_principal_minor=75000,
            term_count=6,
        )

        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        self.assertIsNotNone(loan.id)
        self.assertEqual(loan.application_id, application.id)
        self.assertEqual(loan.customer_id, application.customer_id)
        self.assertEqual(loan.product_id, application.product_id)
        self.assertEqual(loan.approved_principal_minor, 75000)
        self.assertEqual(loan.currency_code, "KES")
        self.assertEqual(loan.interest_method, "reducing")
        self.assertEqual(loan.interest_rate_percent, Decimal("10"))
        self.assertEqual(loan.repayment_frequency, "monthly")
        self.assertEqual(loan.term_count, 6)
        self.assertEqual(loan.principal_minor, 0)
        self.assertEqual(loan.outstanding_principal_minor, 0)
        self.assertEqual(loan.status, "created")
        self.assertEqual(application.status, "approved")

    def test_high_risk_application_cannot_be_approved_with_owner_override(self):
        application = self.create_approved_platform_application()
        application.status = "pending"

        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id
        ).one()
        assessment.status = "high_risk"
        db.session.flush()

        with self.assertRaisesRegex(
            PlatformLendingValidationError,
            "High-risk loan applications cannot be approved",
        ):
            review_loan_application(
                application_id=application.id,
                reviewer_id=self.user.id,
                decision="approved",
                approved_principal_minor=application.requested_principal_minor,
                affordability_override=True,
                decision_notes="Attempted override for regression test.",
            )

        db.session.refresh(application)
        self.assertEqual(application.status, "pending")

    def test_platform_loan_service_requires_affordability_assessment(self):
        application = self.create_approved_platform_application()
        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id
        ).one()
        db.session.delete(assessment)
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            create_platform_loan_from_application(
                application_id=application.id
            )

        self.assertEqual(PlatformLoan.query.filter_by(
            application_id=application.id
        ).count(), 0)

    def test_platform_loan_service_enforces_assessed_affordable_principal(self):
        application = self.create_approved_platform_application(
            requested_principal_minor=100000,
            approved_principal_minor=75000,
        )
        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id
        ).one()
        assessment.estimated_affordable_principal_minor = 50000
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            create_platform_loan_from_application(
                application_id=application.id
            )

        self.assertEqual(PlatformLoan.query.filter_by(
            application_id=application.id
        ).count(), 0)

    def test_high_risk_application_cannot_be_converted_into_platform_loan(self):
        application = self.create_approved_platform_application()
        application.decision_notes = "Reviewer notes must not bypass high-risk block."

        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id
        ).one()
        assessment.status = "high_risk"
        db.session.flush()

        with self.assertRaisesRegex(
            PlatformLendingValidationError,
            "High-risk loan applications cannot be converted",
        ):
            create_platform_loan_from_application(
                application_id=application.id
            )

        self.assertEqual(
            PlatformLoan.query.filter_by(application_id=application.id).count(),
            0,
        )

    def test_platform_loan_service_requires_documented_exception_for_nonverified_assessment(self):
        application = self.create_approved_platform_application()
        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id
        ).one()
        assessment.status = "needs_review"
        application.decision_notes = None
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            create_platform_loan_from_application(
                application_id=application.id
            )

        self.assertEqual(PlatformLoan.query.filter_by(
            application_id=application.id
        ).count(), 0)

    def test_platform_loan_service_rejects_unapproved_application(self):
        application = self.create_approved_platform_application()
        application.status = "pending"
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            create_platform_loan_from_application(
                application_id=application.id
            )

    def test_platform_loan_service_prevents_duplicate_creation(self):
        application = self.create_approved_platform_application()

        first_loan = create_platform_loan_from_application(
            application_id=application.id
        )
        second_attempt = lambda: create_platform_loan_from_application(
            application_id=application.id
        )

        self.assertIsNotNone(first_loan.id)
        with self.assertRaises(PlatformLendingValidationError):
            second_attempt()

    def test_platform_loan_service_rejects_invalid_approved_principal(self):
        application = self.create_approved_platform_application()
        application.approved_principal_minor = None
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            create_platform_loan_from_application(
                application_id=application.id
            )

    def test_platform_loan_partial_disbursement_records_funding_tranche(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        disbursement = disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=40000,
            funding_source="pesapilot_capital",
            disbursement_date=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
            notes="First funding tranche",
        )

        db.session.flush()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(disbursement.amount_minor, 40000)
        self.assertEqual(disbursement.funding_source, "pesapilot_capital")
        self.assertEqual(loan.principal_minor, 40000)
        self.assertEqual(loan.outstanding_principal_minor, 40000)
        self.assertEqual(loan.status, "funding")
        self.assertIsNone(loan.start_date)
        self.assertIsNone(loan.due_date)
        self.assertEqual(
            PlatformLoanInstallment.query.filter_by(loan_id=loan.id).count(),
            0,
        )

        entry = db.session.get(
            PlatformJournalEntry,
            disbursement.journal_entry_id,
        )
        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()

        self.assertEqual(entry.status, "posted")
        self.assertEqual(sum(line.debit_minor for line in lines), 40000)
        self.assertEqual(sum(line.credit_minor for line in lines), 40000)

    def test_mixed_funding_releases_and_restores_each_source_proportionally(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        investor, _contribution = self.create_platform_investor_with_capital(
            account, amount_minor=100000
        )

        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=60000,
            funding_source="pesapilot_capital",
            disbursement_date=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
        )
        db.session.flush()
        self.assertEqual(loan.status, "funding")

        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=40000,
            funding_source="external_investor",
            investor_allocations=[{
                "investor_id": str(investor.id),
                "amount_minor": 40000,
            }],
            disbursement_date=datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
        )
        db.session.flush()

        self.assertEqual(loan.status, "active")
        self.assertEqual(loan.principal_minor, 100000)
        self.assertEqual(loan.outstanding_principal_minor, 100000)

        allocations = PlatformLoanFundingAllocation.query.filter_by(
            loan_id=loan.id
        ).all()
        self.assertEqual(len(allocations), 2)

        pesapilot = next(
            item for item in allocations
            if item.funding_source == "pesapilot_capital"
        )
        external = next(
            item for item in allocations
            if item.funding_source == "external_investor"
        )
        self.assertEqual(pesapilot.outstanding_minor, 60000)
        self.assertEqual(external.outstanding_minor, 40000)
        self.assertIsNone(pesapilot.investor_id)
        self.assertEqual(external.investor_id, investor.id)

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id, installment_number=1
        ).one()
        principal_payment = 10000
        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=installment.interest_minor + principal_payment,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="MIXED-FUNDING-REPAYMENT-001",
        )
        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        releases = PlatformLoanFundingRelease.query.filter_by(
            payment_id=payment.id
        ).all()
        released_by_source = {}
        for release in releases:
            allocation = db.session.get(
                PlatformLoanFundingAllocation,
                release.funding_allocation_id,
            )
            released_by_source[allocation.funding_source] = release.amount_minor

        self.assertEqual(released_by_source, {
            "pesapilot_capital": 6000,
            "external_investor": 4000,
        })
        self.assertEqual(pesapilot.outstanding_minor, 54000)
        self.assertEqual(external.outstanding_minor, 36000)

        reverse_platform_loan_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference="MIXED-FUNDING-REVERSAL-001",
            transaction_date=datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc),
        )
        db.session.flush()

        self.assertEqual(pesapilot.outstanding_minor, 60000)
        self.assertEqual(external.outstanding_minor, 40000)
        self.assertTrue(all(item.is_reversed for item in releases))

    def test_platform_loan_full_disbursement_activates_loan_and_builds_schedule(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        investor, contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=150000,
        )

        disbursement = disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="external_investor",
            investor_allocations=[{
                "investor_id": str(investor.id),
                "amount_minor": 100000,
            }],
            disbursement_date=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
            notes="Investor-funded loan",
        )

        db.session.flush()

        installments = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(PlatformLoanInstallment.installment_number).all()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(disbursement.funding_source, "external_investor")
        self.assertEqual(loan.principal_minor, 100000)
        self.assertEqual(loan.outstanding_principal_minor, 100000)
        self.assertEqual(loan.status, "active")
        self.assertEqual(loan.start_date, date(2026, 11, 16))
        self.assertEqual(loan.due_date, date(2027, 5, 16))
        self.assertEqual(len(installments), 6)
        self.assertEqual(
            sum(item.principal_minor for item in installments),
            100000,
        )
        self.assertEqual(
            sum(item.amount_minor for item in installments),
            sum(item.principal_minor + item.interest_minor for item in installments),
        )

        entry = db.session.get(
            PlatformJournalEntry,
            disbursement.journal_entry_id,
        )
        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()

        self.assertEqual(entry.status, "posted")
        self.assertEqual(sum(line.debit_minor for line in lines), 100000)
        self.assertEqual(sum(line.credit_minor for line in lines), 100000)

    def test_platform_loan_partial_disbursement_records_funding_tranche(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        disbursement = disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=40000,
            funding_source="pesapilot_capital",
            disbursement_date=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
            notes="First funding tranche",
        )

        db.session.flush()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(disbursement.amount_minor, 40000)
        self.assertEqual(disbursement.funding_source, "pesapilot_capital")
        self.assertEqual(loan.principal_minor, 40000)
        self.assertEqual(loan.outstanding_principal_minor, 40000)
        self.assertEqual(loan.status, "funding")
        self.assertIsNone(loan.start_date)
        self.assertIsNone(loan.due_date)
        self.assertEqual(
            PlatformLoanInstallment.query.filter_by(loan_id=loan.id).count(),
            0,
        )

        entry = db.session.get(
            PlatformJournalEntry,
            disbursement.journal_entry_id,
        )
        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()

        self.assertEqual(entry.status, "posted")
        self.assertEqual(sum(line.debit_minor for line in lines), 40000)
        self.assertEqual(sum(line.credit_minor for line in lines), 40000)

    def test_platform_loan_full_disbursement_activates_loan_and_builds_schedule(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        investor, contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=150000,
        )

        disbursement = disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="external_investor",
            investor_allocations=[{
                "investor_id": str(investor.id),
                "amount_minor": 100000,
            }],
            disbursement_date=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
            recorded_by=self.user.id,
            notes="Investor-funded loan",
        )

        db.session.flush()

        installments = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(PlatformLoanInstallment.installment_number).all()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(disbursement.funding_source, "external_investor")
        self.assertEqual(loan.principal_minor, 100000)
        self.assertEqual(loan.outstanding_principal_minor, 100000)
        self.assertEqual(loan.status, "active")
        self.assertEqual(loan.start_date, date(2026, 11, 16))
        self.assertEqual(loan.due_date, date(2027, 5, 16))
        self.assertEqual(len(installments), 6)
        self.assertEqual(
            sum(item.principal_minor for item in installments),
            100000,
        )
        self.assertEqual(
            sum(item.amount_minor for item in installments),
            sum(item.principal_minor + item.interest_minor for item in installments),
        )

        entry = db.session.get(
            PlatformJournalEntry,
            disbursement.journal_entry_id,
        )
        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()

        self.assertEqual(entry.status, "posted")
        self.assertEqual(sum(line.debit_minor for line in lines), 100000)
        self.assertEqual(sum(line.credit_minor for line in lines), 100000)

    def test_platform_loan_disbursement_rejects_amount_above_approval(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        with self.assertRaises(PlatformLendingValidationError):
            disburse_platform_loan(
                loan_id=loan.id,
                financial_account_id=account.id,
                customer_financial_account_id=self.source_account.id,
                amount_minor=100001,
                funding_source="pesapilot_capital",
                disbursement_date=datetime.now(timezone.utc),
                recorded_by=self.user.id,
            )

        self.assertEqual(loan.principal_minor, 0)
        self.assertEqual(loan.status, "created")
        self.assertEqual(
            PlatformLoanDisbursement.query.filter_by(loan_id=loan.id).count(),
            0,
        )

    def test_platform_loan_disbursement_rejects_invalid_funding_source(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        with self.assertRaises(PlatformLendingValidationError):
            disburse_platform_loan(
                loan_id=loan.id,
                financial_account_id=account.id,
                customer_financial_account_id=self.source_account.id,
                amount_minor=100000,
                funding_source="unknown_source",
                disbursement_date=datetime.now(timezone.utc),
                recorded_by=self.user.id,
            )

        self.assertEqual(loan.principal_minor, 0)
        self.assertEqual(loan.status, "created")

    def test_one_time_platform_loan_uses_product_maturity_snapshot(self):
        account = self.create_platform_accounting_setup()
        product = self.create_platform_loan_product(
            interest_method="flat",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="one_time",
            min_term_count=1,
            max_term_count=1,
            one_time_due_days=30,
        )
        application = self.create_approved_platform_application(
            term_count=1,
            product=product,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        self.assertEqual(loan.one_time_due_days, 30)

        # Later product edits must not change an existing loan's terms.
        product.one_time_due_days = 60
        db.session.flush()
        self.assertEqual(product.one_time_due_days, 60)
        self.assertEqual(loan.one_time_due_days, 30)

        funding_date = datetime(
            2026, 11, 16, 9, 0, tzinfo=timezone.utc
        )
        disbursement = disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="pesapilot_capital",
            disbursement_date=funding_date,
            recorded_by=self.user.id,
        )

        expected_due_date = date(2026, 12, 16)
        installments = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).all()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(loan.status, "active")
        self.assertEqual(loan.start_date, date(2026, 11, 16))
        self.assertEqual(loan.due_date, expected_due_date)
        self.assertEqual(len(installments), 1)
        self.assertEqual(installments[0].due_date, expected_due_date)
        self.assertEqual(installments[0].principal_minor, 100000)

    def create_funded_platform_loan_for_repayment(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="pesapilot_capital",
            disbursement_date=datetime(
                2026, 11, 16, 9, 0, tzinfo=timezone.utc
            ),
            recorded_by=self.user.id,
        )
        db.session.flush()
        return loan, account

    def test_platform_loan_records_partial_repayment_as_pending(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()
        installments = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(PlatformLoanInstallment.installment_number).all()
        original_outstanding = loan.outstanding_principal_minor
        original_paid = [item.paid_minor for item in installments]

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="PARTIAL-001",
            notes="Partial customer repayment",
        )

        self.assertIsNotNone(payment.id)
        self.assertEqual(payment.amount_minor, 1000)
        self.assertEqual(payment.currency_code, "KES")
        self.assertEqual(payment.payment_method, "mobile_money")
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.reference, "PARTIAL-001")
        self.assertIsNone(payment.financial_account_id)
        self.assertIsNone(payment.journal_entry_id)
        self.assertEqual(loan.outstanding_principal_minor, original_outstanding)
        self.assertEqual(
            [item.paid_minor for item in installments],
            original_paid,
        )
        self.assertEqual(
            PlatformLoanPaymentAllocation.query.filter_by(
                loan_id=loan.id
            ).count(),
            0,
        )

    def test_platform_loan_repayment_rejects_invalid_amount(self):
        for invalid_amount in (0, -1, 1.5, True):
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(PlatformLendingValidationError):
                    record_platform_loan_payment(
                        loan_id=uuid4(),
                        amount_minor=invalid_amount,
                        payment_date=date(2026, 11, 17),
                        payment_method="cash",
                        recorded_by=self.user.id,
                    )

    def test_platform_loan_repayment_rejects_amount_above_schedule_balance(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()
        outstanding_schedule = sum(
            item.amount_minor - item.paid_minor
            for item in PlatformLoanInstallment.query.filter_by(
                loan_id=loan.id
            ).all()
        )

        with self.assertRaises(PlatformLendingValidationError):
            record_platform_loan_payment(
                loan_id=loan.id,
                amount_minor=outstanding_schedule + 1,
                payment_date=date(2026, 11, 17),
                payment_method="cash",
                recorded_by=self.user.id,
            )

        self.assertEqual(
            PlatformLoanPayment.query.filter_by(loan_id=loan.id).count(),
            0,
        )
        self.assertEqual(loan.outstanding_principal_minor, 100000)

    def test_platform_loan_repayment_rejects_duplicate_reference(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()
        payment_args = {
            "loan_id": loan.id,
            "amount_minor": 1000,
            "payment_date": date(2026, 11, 17),
            "payment_method": "mobile_money",
            "recorded_by": self.user.id,
            "reference": "DUPLICATE-001",
        }

        first_payment = record_platform_loan_payment(**payment_args)
        self.assertEqual(first_payment.status, "pending")

        with self.assertRaises(PlatformLendingValidationError):
            record_platform_loan_payment(**payment_args)


    def test_platform_loan_repayment_confirmation_posts_balanced_entry(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        first_installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        self.assertGreater(first_installment.interest_minor, 0)
        self.assertGreater(first_installment.principal_minor, 100)

        interest_due = first_installment.interest_minor
        payment_amount = interest_due + 100
        original_outstanding = loan.outstanding_principal_minor

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=payment_amount,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="CONFIRM-001",
        )

        confirmed = confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )

        db.session.flush()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.amount_minor, payment_amount)
        self.assertEqual(confirmed.interest_minor, interest_due)
        self.assertEqual(confirmed.principal_minor, 100)
        self.assertEqual(confirmed.financial_account_id, account.id)
        self.assertIsNotNone(confirmed.journal_entry_id)
        self.assertIsNotNone(confirmed.confirmed_at)

        self.assertEqual(
            loan.outstanding_principal_minor,
            original_outstanding - 100,
        )

        self.assertEqual(
            first_installment.paid_minor,
            payment_amount,
        )
        self.assertEqual(first_installment.status, "partially_paid")

        allocations = PlatformLoanPaymentAllocation.query.filter_by(
            payment_id=payment.id
        ).all()

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].interest_minor, interest_due)
        self.assertEqual(allocations[0].principal_minor, 100)

        journal_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=payment.journal_entry_id
        ).all()

        self.assertEqual(len(journal_lines), 3)
        self.assertEqual(
            sum(line.debit_minor for line in journal_lines),
            payment_amount,
        )
        self.assertEqual(
            sum(line.credit_minor for line in journal_lines),
            payment_amount,
        )

        cash_line = next(
            line for line in journal_lines
            if line.ledger_account_id == self.platform_cash_ledger.id
        )
        receivable_line = next(
            line for line in journal_lines
            if line.ledger_account_id == self.platform_receivable_ledger.id
        )
        interest_line = next(
            line for line in journal_lines
            if line.ledger_account_id
            == self.platform_interest_income_ledger.id
        )

        self.assertEqual(cash_line.debit_minor, payment_amount)
        self.assertEqual(receivable_line.credit_minor, 100)
        self.assertEqual(interest_line.credit_minor, interest_due)

        with self.assertRaises(PlatformLendingValidationError):
            confirm_platform_loan_payment(
                payment_id=payment.id,
                financial_account_id=account.id,
                confirmed_by=self.user.id,
            )

    def test_platform_loan_mobile_money_repayment_can_be_received_into_bank_account(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        account.account_type = "bank"
        db.session.flush()

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=installment.interest_minor + 100,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="MOBILE-TO-BANK-001",
        )

        confirmed = confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )

        db.session.flush()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.payment_method, "mobile_money")
        self.assertEqual(confirmed.financial_account_id, account.id)
        self.assertIsNotNone(confirmed.journal_entry_id)

        journal_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=confirmed.journal_entry_id
        ).all()

        self.assertEqual(
            sum(line.debit_minor for line in journal_lines),
            confirmed.amount_minor,
        )
        self.assertEqual(
            sum(line.credit_minor for line in journal_lines),
            confirmed.amount_minor,
        )
    def test_platform_loan_repayment_reversal_restores_balances(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        payment_amount = installment.interest_minor + 100
        original_outstanding = loan.outstanding_principal_minor
        original_paid = installment.paid_minor

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=payment_amount,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="REVERSAL-001",
        )

        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        original_journal_id = payment.journal_entry_id
        self.assertEqual(payment.status, "confirmed")
        self.assertEqual(installment.paid_minor, payment_amount)
        self.assertEqual(
            loan.outstanding_principal_minor,
            original_outstanding - 100,
        )

        reversal_date = datetime(
            2026, 11, 18, 10, 0, tzinfo=timezone.utc
        )
        reversal = reverse_platform_loan_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference="REVERSAL-JOURNAL-001",
            transaction_date=reversal_date,
        )
        db.session.flush()

        self.assertEqual(payment.status, "reversed")
        self.assertIsNotNone(payment.reversed_at)
        self.assertEqual(installment.paid_minor, original_paid)
        self.assertEqual(
            loan.outstanding_principal_minor,
            original_outstanding,
        )

        original_journal = db.session.get(
            PlatformJournalEntry,
            original_journal_id,
        )
        self.assertEqual(original_journal.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(reversal.reversal_of_id, original_journal_id)

        original_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=original_journal_id
        ).all()
        reversal_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=reversal.id
        ).all()

        self.assertEqual(len(original_lines), len(reversal_lines))
        self.assertEqual(
            sum(line.debit_minor for line in reversal_lines),
            payment_amount,
        )
        self.assertEqual(
            sum(line.credit_minor for line in reversal_lines),
            payment_amount,
        )

        self.assertEqual(
            PlatformLoanPaymentAllocation.query.filter_by(
                payment_id=payment.id
            ).count(),
            1,
        )

    def test_investor_funding_is_released_proportionally_and_restored_on_reversal(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        investor_a, _contribution_a = (
            self.create_platform_investor_with_capital(
                account, amount_minor=60000
            )
        )
        investor_b, _contribution_b = (
            self.create_platform_investor_with_capital(
                account, amount_minor=40000
            )
        )

        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="external_investor",
            investor_allocations=[
                {
                    "investor_id": investor_a.id,
                    "amount_minor": 60000,
                },
                {
                    "investor_id": investor_b.id,
                    "amount_minor": 40000,
                },
            ],
            disbursement_date=datetime(
                2026, 11, 16, 9, 0, tzinfo=timezone.utc
            ),
            recorded_by=self.user.id,
        )
        db.session.flush()

        allocations = PlatformLoanFundingAllocation.query.filter_by(
            loan_id=loan.id
        ).all()
        self.assertEqual(len(allocations), 2)

        original_outstanding = {
            item.investor_id: item.outstanding_minor
            for item in allocations
        }
        self.assertEqual(original_outstanding[investor_a.id], 60000)
        self.assertEqual(original_outstanding[investor_b.id], 40000)

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()
        principal_payment = 10000
        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=installment.interest_minor + principal_payment,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="INVESTOR-RELEASE-001",
        )
        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        releases = PlatformLoanFundingRelease.query.filter_by(
            payment_id=payment.id
        ).all()
        released_by_investor = {}
        for release in releases:
            allocation = db.session.get(
                PlatformLoanFundingAllocation,
                release.funding_allocation_id,
            )
            released_by_investor[allocation.investor_id] = (
                release.amount_minor
            )

        self.assertEqual(
            released_by_investor,
            {
                investor_a.id: 6000,
                investor_b.id: 4000,
            },
        )

        remaining_by_investor = {
            item.investor_id: item.outstanding_minor
            for item in allocations
        }
        self.assertEqual(remaining_by_investor[investor_a.id], 54000)
        self.assertEqual(remaining_by_investor[investor_b.id], 36000)

        reverse_platform_loan_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference="INVESTOR-RELEASE-REV-001",
            transaction_date=datetime(
                2026, 11, 18, 9, 0, tzinfo=timezone.utc
            ),
        )
        db.session.flush()

        restored_by_investor = {
            item.investor_id: item.outstanding_minor
            for item in allocations
        }
        self.assertEqual(restored_by_investor, original_outstanding)
        self.assertTrue(all(release.is_reversed for release in releases))
        self.assertTrue(
            all(release.reversed_at is not None for release in releases)
        )

    def test_investor_funding_reversal_rejects_missing_release_history(self):
        account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        investor, _contribution = self.create_platform_investor_with_capital(
            account, amount_minor=100000
        )

        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="external_investor",
            investor_allocations=[
                {
                    "investor_id": investor.id,
                    "amount_minor": 100000,
                }
            ],
            disbursement_date=datetime(
                2026, 11, 16, 9, 0, tzinfo=timezone.utc
            ),
            recorded_by=self.user.id,
        )
        db.session.flush()

        allocation = PlatformLoanFundingAllocation.query.filter_by(
            loan_id=loan.id
        ).one()
        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        principal_payment = 10000
        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=installment.interest_minor + principal_payment,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="MISSING-RELEASE-001",
        )
        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        self.assertGreater(
            PlatformLoanFundingRelease.query.filter_by(
                payment_id=payment.id
            ).count(),
            0,
        )
        db.session.query(PlatformLoanFundingRelease).filter_by(
            payment_id=payment.id
        ).delete(synchronize_session=False)
        db.session.flush()

        outstanding_before = allocation.outstanding_minor
        principal_before = loan.outstanding_principal_minor
        paid_before = installment.paid_minor
        journal = db.session.get(
            PlatformJournalEntry,
            payment.journal_entry_id,
        )

        with self.assertRaisesRegex(
            PlatformLendingValidationError,
            "no active release records",
        ):
            reverse_platform_loan_payment(
                payment_id=payment.id,
                reversed_by=self.user.id,
                reference="MISSING-RELEASE-REV-001",
            )

        self.assertEqual(payment.status, "confirmed")
        self.assertEqual(allocation.outstanding_minor, outstanding_before)
        self.assertEqual(loan.outstanding_principal_minor, principal_before)
        self.assertEqual(installment.paid_minor, paid_before)
        self.assertEqual(journal.status, "posted")

    def test_platform_loan_repayment_cannot_reverse_before_later_payment(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        first = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="ORDER-001",
        )
        confirm_platform_loan_payment(
            payment_id=first.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )

        second = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="ORDER-002",
        )
        confirm_platform_loan_payment(
            payment_id=second.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        with self.assertRaisesRegex(
            PlatformLendingValidationError,
            "later confirmed repayment exists",
        ):
            reverse_platform_loan_payment(
                payment_id=first.id,
                reversed_by=self.user.id,
                reference="ORDER-REV-001",
            )

        self.assertEqual(first.status, "confirmed")
        self.assertEqual(second.status, "confirmed")

    def test_platform_loan_repayment_cannot_be_reversed_twice(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="DOUBLE-REV-001",
        )
        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )

        reversal = reverse_platform_loan_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference="DOUBLE-REV-JOURNAL-001",
        )
        db.session.flush()

        original_journal_id = payment.journal_entry_id
        reversal_id = reversal.id

        with self.assertRaisesRegex(
            PlatformLendingValidationError,
            "Only confirmed platform loan payments can be reversed",
        ):
            reverse_platform_loan_payment(
                payment_id=payment.id,
                reversed_by=self.user.id,
                reference="DOUBLE-REV-JOURNAL-002",
            )

        self.assertEqual(payment.status, "reversed")
        self.assertEqual(payment.journal_entry_id, original_journal_id)
        self.assertEqual(
            db.session.get(PlatformJournalEntry, original_journal_id).status,
            "reversed",
        )
        self.assertIsNotNone(db.session.get(PlatformJournalEntry, reversal_id))

    def test_platform_loan_record_can_be_created_from_approved_application(self):
        application = self.create_approved_platform_application()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=application.approved_principal_minor,
            principal_minor=0,
            outstanding_principal_minor=0,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="created",
        )
        db.session.add(loan)
        db.session.flush()

        self.assertIsNotNone(loan.id)
        self.assertEqual(loan.approved_principal_minor, 100000)
        self.assertEqual(loan.principal_minor, 0)
        self.assertEqual(loan.outstanding_principal_minor, 0)
        self.assertEqual(loan.status, "created")

    def test_platform_application_cannot_have_two_loan_records(self):
        application = self.create_approved_platform_application()

        values = {
            "application_id": application.id,
            "customer_id": application.customer_id,
            "product_id": application.product_id,
            "approved_principal_minor": application.approved_principal_minor,
            "principal_minor": 0,
            "outstanding_principal_minor": 0,
            "currency_code": "KES",
            "interest_method": "reducing",
            "interest_rate_percent": Decimal("10"),
            "repayment_frequency": "monthly",
            "term_count": application.term_count,
            "status": "created",
        }
        db.session.add(PlatformLoan(**values))
        db.session.flush()

        db.session.add(PlatformLoan(**values))
        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_loan_cannot_exceed_approved_principal(self):
        application = self.create_approved_platform_application()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=100001,
            outstanding_principal_minor=100001,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="funding",
        )
        db.session.add(loan)

        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_loan_outstanding_cannot_exceed_disbursed_principal(self):
        application = self.create_approved_platform_application()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=50000,
            outstanding_principal_minor=50001,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=application.term_count,
            status="funding",
        )
        db.session.add(loan)

        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_loan_installment_can_be_created(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=100000,
            outstanding_principal_minor=100000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        installment = PlatformLoanInstallment(
            loan_id=loan.id,
            installment_number=1,
            due_date=date(2027, 1, 15),
            amount_minor=18000,
            principal_minor=15000,
            interest_minor=3000,
        )
        db.session.add(installment)
        db.session.flush()

        self.assertIsNotNone(installment.id)
        self.assertEqual(installment.amount_minor, 18000)
        self.assertEqual(
            installment.amount_minor,
            installment.principal_minor + installment.interest_minor,
        )
        self.assertEqual(installment.paid_minor, 0)
        self.assertEqual(installment.status, "scheduled")

    def test_platform_loan_cannot_have_duplicate_installment_numbers(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=100000,
            outstanding_principal_minor=100000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        values = {
            "loan_id": loan.id,
            "installment_number": 1,
            "due_date": date(2027, 1, 15),
            "amount_minor": 18000,
            "principal_minor": 15000,
            "interest_minor": 3000,
        }
        db.session.add(PlatformLoanInstallment(**values))
        db.session.flush()

        db.session.add(PlatformLoanInstallment(**values))
        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_installment_paid_amount_cannot_exceed_amount(self):
        application = self.create_approved_platform_application()
        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=100000,
            outstanding_principal_minor=100000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        installment = PlatformLoanInstallment(
            loan_id=loan.id,
            installment_number=1,
            due_date=date(2027, 1, 15),
            amount_minor=18000,
            principal_minor=15000,
            interest_minor=3000,
            paid_minor=18001,
        )
        db.session.add(installment)

        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_loan_disbursement_can_be_recorded(self):
        application = self.create_approved_platform_application()
        financial_account = self.create_platform_accounting_setup()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=50000,
            outstanding_principal_minor=50000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="funding",
        )
        db.session.add(loan)
        db.session.flush()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-DISB-{uuid4().hex[:12]}",
            description="Platform loan disbursement test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 50000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 50000,
                },
            ],
        )
        db.session.flush()

        disbursement = PlatformLoanDisbursement(
            loan_id=loan.id,
            financial_account_id=financial_account.id,
            journal_entry_id=entry.id,
            currency_code="KES",
            amount_minor=50000,
            funding_source="pesapilot_capital",
            disbursement_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
        )
        db.session.add(disbursement)
        db.session.flush()

        self.assertIsNotNone(disbursement.id)
        self.assertEqual(disbursement.amount_minor, 50000)
        self.assertEqual(disbursement.funding_source, "pesapilot_capital")

    def test_platform_disbursement_rejects_unsupported_funding_source(self):
        application = self.create_approved_platform_application()
        financial_account = self.create_platform_accounting_setup()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=50000,
            outstanding_principal_minor=50000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="funding",
        )
        db.session.add(loan)
        db.session.flush()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-DISB-{uuid4().hex[:12]}",
            description="Invalid funding source test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 50000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 50000,
                },
            ],
        )
        db.session.flush()

        db.session.add(PlatformLoanDisbursement(
            loan_id=loan.id,
            financial_account_id=financial_account.id,
            journal_entry_id=entry.id,
            currency_code="KES",
            amount_minor=50000,
            funding_source="unknown_source",
            disbursement_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
        ))

        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_disbursement_rejects_currency_mismatch(self):
        application = self.create_approved_platform_application()
        self.create_platform_accounting_setup()

        loan = PlatformLoan(
            application_id=application.id,
            customer_id=application.customer_id,
            product_id=application.product_id,
            approved_principal_minor=100000,
            principal_minor=50000,
            outstanding_principal_minor=50000,
            currency_code="KES",
            interest_method="reducing",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="monthly",
            term_count=6,
            status="funding",
        )
        db.session.add(loan)

        db.session.add(Currency(
            code="USD",
            name="US Dollar",
            minor_unit=2,
            is_active=True,
        ))
        db.session.flush()

        usd_account = PlatformFinancialAccount(
            name="USD Test Account",
            account_type="bank",
            currency_code="USD",
            is_active=True,
        )
        db.session.add(usd_account)
        db.session.flush()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-DISB-{uuid4().hex[:12]}",
            description="Currency mismatch test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 50000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 50000,
                },
            ],
        )
        db.session.flush()

        db.session.add(PlatformLoanDisbursement(
            loan_id=loan.id,
            financial_account_id=usd_account.id,
            journal_entry_id=entry.id,
            currency_code="USD",
            amount_minor=50000,
            funding_source="external_investor",
            disbursement_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
        ))

        with self.assertRaises(Exception):
            db.session.flush()
        db.session.rollback()

    def test_platform_accounting_creates_independent_draft_entry(self):
        self.create_platform_accounting_setup()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-TEST-{uuid4().hex[:12]}",
            description="Platform test opening entry",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 100000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 100000,
                },
            ],
        )
        db.session.flush()

        self.assertEqual(entry.status, "draft")
        self.assertEqual(entry.functional_currency_code, "KES")
        self.assertEqual(len(entry.lines) if hasattr(entry, "lines") else (
            PlatformJournalLine.query.filter_by(
                journal_entry_id=entry.id
            ).count()
        ), 2)

        audit = AuditLog.query.filter_by(
            workspace_id=None,
            action="platform_journal_entry_created",
            resource_id=str(entry.id),
        ).one()
        self.assertIsNone(audit.workspace_id)

        line_accounts = {
            line.ledger_account_id
            for line in PlatformJournalLine.query.filter_by(
                journal_entry_id=entry.id
            ).all()
        }
        self.assertEqual(
            line_accounts,
            {
                self.platform_cash_ledger.id,
                self.platform_equity_ledger.id,
            },
        )
        self.assertNotIn(self.source_ledger.id, line_accounts)

    def test_platform_accounting_posts_balanced_entry(self):
        self.create_platform_accounting_setup()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-POST-{uuid4().hex[:12]}",
            description="Platform posting test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 75000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 75000,
                },
            ],
        )

        posted = post_platform_entry(
            entry_id=entry.id,
            posted_by=self.user.id,
        )
        db.session.flush()

        self.assertEqual(posted.status, "posted")
        self.assertEqual(posted.posted_by, self.user.id)
        self.assertIsNotNone(posted.posted_at)

        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()
        self.assertEqual(
            sum(line.functional_debit_minor for line in lines),
            75000,
        )
        self.assertEqual(
            sum(line.functional_credit_minor for line in lines),
            75000,
        )

        audit = AuditLog.query.filter_by(
            workspace_id=None,
            action="platform_journal_entry_posted",
            resource_id=str(entry.id),
        ).one()
        self.assertIsNone(audit.workspace_id)

    def test_platform_accounting_reverses_posted_entry(self):
        self.create_platform_accounting_setup()

        original = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-ORIG-{uuid4().hex[:12]}",
            description="Platform reversal test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 42000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 42000,
                },
            ],
        )
        post_platform_entry(
            entry_id=original.id,
            posted_by=self.user.id,
        )

        reversal = reverse_platform_entry(
            entry_id=original.id,
            reversed_by=self.user.id,
            reference=f"PL-REV-{uuid4().hex[:12]}",
        )
        db.session.flush()

        self.assertEqual(original.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(reversal.reversal_of_id, original.id)
        self.assertEqual(reversal.created_by, self.user.id)
        self.assertEqual(reversal.posted_by, self.user.id)

        original_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=original.id
        ).all()
        reversal_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=reversal.id
        ).all()

        self.assertEqual(len(original_lines), len(reversal_lines))
        for original_line in original_lines:
            reversed_line = next(
                line for line in reversal_lines
                if line.ledger_account_id == original_line.ledger_account_id
            )
            self.assertEqual(
                reversed_line.debit_minor,
                original_line.credit_minor,
            )
            self.assertEqual(
                reversed_line.credit_minor,
                original_line.debit_minor,
            )
            self.assertEqual(
                reversed_line.functional_debit_minor,
                original_line.functional_credit_minor,
            )
            self.assertEqual(
                reversed_line.functional_credit_minor,
                original_line.functional_debit_minor,
            )

        original_audit = AuditLog.query.filter_by(
            workspace_id=None,
            action="platform_journal_entry_reversed",
            resource_id=str(original.id),
        ).one()
        reversal_audit = AuditLog.query.filter_by(
            workspace_id=None,
            action="platform_journal_reversal_created",
            resource_id=str(reversal.id),
        ).one()
        self.assertIsNone(original_audit.workspace_id)
        self.assertIsNone(reversal_audit.workspace_id)

    def test_platform_accounting_rejects_unbalanced_entry(self):
        self.create_platform_accounting_setup()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-UNBAL-{uuid4().hex[:12]}",
            description="Platform unbalanced posting test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 50000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 50000,
                },
            ],
        )

        line = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id,
            ledger_account_id=self.platform_equity_ledger.id,
        ).one()
        line.functional_credit_minor = 49000
        db.session.flush()

        with self.assertRaises(PlatformAccountingError):
            post_platform_entry(
                entry_id=entry.id,
                posted_by=self.user.id,
            )

        self.assertEqual(entry.status, "draft")
        self.assertIsNone(entry.posted_at)

    def test_platform_accounting_rejects_workspace_ledger_accounts(self):
        self.create_platform_accounting_setup()

        with self.assertRaises(PlatformAccountingError):
            create_platform_draft_entry(
                created_by=self.user.id,
                reference=f"PL-SCOPE-{uuid4().hex[:12]}",
                description="Reject workspace ledger test",
                transaction_date=datetime.now(timezone.utc),
                lines=[
                    {
                        "ledger_account_id": str(self.source_ledger.id),
                        "debit_minor": 30000,
                        "credit_minor": 0,
                    },
                    {
                        "ledger_account_id": str(self.platform_equity_ledger.id),
                        "debit_minor": 0,
                        "credit_minor": 30000,
                    },
                ],
            )

    def test_platform_accounting_cannot_reverse_draft_entry(self):
        self.create_platform_accounting_setup()

        entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"PL-DRAFT-{uuid4().hex[:12]}",
            description="Platform draft reversal rejection test",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": str(self.platform_cash_ledger.id),
                    "debit_minor": 18000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.platform_equity_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": 18000,
                },
            ],
        )

        with self.assertRaises(PlatformAccountingError):
            reverse_platform_entry(
                entry_id=entry.id,
                reversed_by=self.user.id,
                reference=f"PL-REV-DRAFT-{uuid4().hex[:8]}",
            )

        self.assertEqual(entry.status, "draft")
        self.assertIsNone(entry.posted_at)

    def test_workspace_owner_cannot_access_platform_owner_endpoint(self):
        response = self.client.get(
            "/api/v1/platform/owner/access",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json["success"])

    def test_platform_owner_can_access_owner_endpoint(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/access",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["role"], "owner")

    def test_platform_owner_endpoint_requires_authentication(self):
        response = self.client.get(
            "/api/v1/platform/owner/access",
        )

        self.assertEqual(response.status_code, 401)


    def test_platform_owner_can_configure_functional_currency(self):
        # The test database is shared across methods; reset this singleton
        # so this test can verify the unconfigured state.
        existing_settings = db.session.get(
            PlatformAccountingSettings, 1
        )
        if existing_settings is not None:
            db.session.delete(existing_settings)
            db.session.commit()

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        before = self.client.get(
            "/api/v1/platform/owner/accounting-settings",
            headers=self.headers,
        )
        self.assertEqual(before.status_code, 200, before.json)
        self.assertFalse(before.json["configured"])
        self.assertIsNone(before.json["functional_currency"])

        saved = self.client.put(
            "/api/v1/platform/owner/accounting-settings",
            headers=self.headers,
            json={"functional_currency_code": "kes"},
        )
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertTrue(saved.json["success"])
        self.assertEqual(
            saved.json["functional_currency"]["code"], "KES"
        )

        settings = db.session.get(PlatformAccountingSettings, 1)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.functional_currency_code, "KES")
        self.assertEqual(settings.updated_by, self.user.id)

        after = self.client.get(
            "/api/v1/platform/owner/accounting-settings",
            headers=self.headers,
        )
        self.assertEqual(after.status_code, 200, after.json)
        self.assertTrue(after.json["configured"])
        self.assertEqual(
            after.json["functional_currency"]["code"], "KES"
        )

    def test_platform_accounting_settings_reject_invalid_currency(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.add(Currency(
            code="QXZ",
            name="Inactive Test Currency",
            minor_unit=2,
            is_active=False,
        ))
        db.session.commit()

        existing_settings = db.session.get(
            PlatformAccountingSettings, 1
        )
        existing_currency = (
            existing_settings.functional_currency_code
            if existing_settings else None
        )

        for code in ("ZZZ", "QXZ", "KE"):
            response = self.client.put(
                "/api/v1/platform/owner/accounting-settings",
                headers=self.headers,
                json={"functional_currency_code": code},
            )
            self.assertEqual(response.status_code, 400, response.json)

        current_settings = db.session.get(
            PlatformAccountingSettings, 1
        )
        if existing_currency is None:
            self.assertIsNone(current_settings)
        else:
            self.assertIsNotNone(current_settings)
            self.assertEqual(
                current_settings.functional_currency_code,
                existing_currency,
            )

    def test_platform_accounting_settings_require_owner_access(self):
        url = "/api/v1/platform/owner/accounting-settings"

        for method in ("get", "put"):
            unauthenticated = getattr(self.client, method)(url)
            self.assertEqual(unauthenticated.status_code, 401)

            non_owner = getattr(self.client, method)(
                url,
                headers=self.headers,
                json={"functional_currency_code": "KES"},
            ) if method == "put" else getattr(self.client, method)(
                url,
                headers=self.headers,
            )
            self.assertEqual(non_owner.status_code, 403)

    def test_platform_functional_currency_is_locked_after_journal_history(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.add(Currency(
            code="USD",
            name="US Dollar",
            minor_unit=2,
            is_active=True,
        ))
        settings = db.session.get(PlatformAccountingSettings, 1)
        if settings is None:
            settings = PlatformAccountingSettings(
                id=1,
                functional_currency_code="KES",
                updated_by=self.user.id,
            )
            db.session.add(settings)
        else:
            settings.functional_currency_code = "KES"
            settings.updated_by = self.user.id

        db.session.flush()

        db.session.add(PlatformJournalEntry(
            reference=f"PL-SETTINGS-LOCK-{uuid4().hex}",
            description="Journal history for currency lock test",
            transaction_date=datetime.now(timezone.utc),
            functional_currency_code="KES",
            created_by=self.user.id,
        ))
        db.session.commit()

        response = self.client.put(
            "/api/v1/platform/owner/accounting-settings",
            headers=self.headers,
            json={"functional_currency_code": "USD"},
        )

        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(
            db.session.get(
                PlatformAccountingSettings, 1
            ).functional_currency_code,
            "KES",
        )

    def test_platform_owner_can_list_active_financial_accounts(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))

        active_account = PlatformFinancialAccount(
            name="PesaPilot Main Bank",
            account_type="bank",
            currency_code="KES",
            institution_name="Test Bank",
            display_reference="MAIN-001",
            is_active=True,
        )
        inactive_account = PlatformFinancialAccount(
            name="Inactive Cash Account",
            account_type="cash",
            currency_code="KES",
            is_active=False,
        )
        db.session.add_all([active_account, inactive_account])
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        accounts = response.json["accounts"]
        account_ids = [item["id"] for item in accounts]

        self.assertIn(str(active_account.id), account_ids)
        self.assertNotIn(str(inactive_account.id), account_ids)

        listed = next(
            item for item in accounts
            if item["id"] == str(active_account.id)
        )
        self.assertEqual(listed["name"], "PesaPilot Main Bank")
        self.assertEqual(listed["account_type"], "bank")
        self.assertEqual(listed["currency_code"], "KES")
        self.assertEqual(listed["institution_name"], "Test Bank")
        self.assertEqual(listed["display_reference"], "MAIN-001")

        filtered = self.client.get(
            "/api/v1/platform/owner/financial-accounts?currency_code=kes",
            headers=self.headers,
        )
        self.assertEqual(filtered.status_code, 200, filtered.json)
        self.assertTrue(all(
            item["currency_code"] == "KES"
            for item in filtered.json["accounts"]
        ))

    def test_platform_owner_can_create_financial_account_with_asset_ledger(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        # Use a separate currency to verify automatic capital-ledger creation.
        usd_currency = db.session.get(Currency, "USD")
        if usd_currency is None:
            db.session.add(Currency(
                code="USD",
                name="US Dollar",
                minor_unit=2,
                is_active=True,
            ))
        else:
            usd_currency.is_active = True
        db.session.commit()

        response = self.client.post(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
            json={
                "name": "PesaPilot Main Account",
                "account_type": "bank",
                "currency_code": "USD",
                "institution_name": "Test Bank",
                "display_reference": "MAIN-001",
            },
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        account_data = response.json["account"]
        account = db.session.get(
            PlatformFinancialAccount,
            UUID(account_data["id"]),
        )
        self.assertIsNotNone(account)
        self.assertEqual(account.name, "PesaPilot Main Account")
        self.assertEqual(account.account_type, "bank")
        self.assertEqual(account.currency_code, "USD")
        self.assertTrue(account.is_active)

        ledger = PlatformLedgerAccount.query.filter_by(
            financial_account_id=account.id
        ).one()

        self.assertEqual(account_data["ledger_account_id"], str(ledger.id))
        self.assertEqual(ledger.account_class, "asset")
        self.assertEqual(ledger.normal_balance, "debit")
        self.assertEqual(ledger.currency_code, "USD")
        self.assertFalse(ledger.is_system)
        self.assertTrue(ledger.is_active)
        self.assertTrue(ledger.code.startswith("PL-BANK-"))

        capital_ledger = PlatformLedgerAccount.query.filter_by(
            code="PL-INVESTOR-CAPITAL-USD"
        ).one()
        self.assertIsNone(capital_ledger.financial_account_id)
        self.assertEqual(capital_ledger.account_class, "liability")
        self.assertEqual(capital_ledger.normal_balance, "credit")
        self.assertEqual(capital_ledger.currency_code, "USD")
        self.assertTrue(capital_ledger.is_system)
        self.assertTrue(capital_ledger.is_active)

        listed = self.client.get(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
        )
        self.assertEqual(listed.status_code, 200, listed.json)
        self.assertIn(
            account_data["id"],
            [item["id"] for item in listed.json["accounts"]],
        )

    def test_platform_owner_cannot_create_account_with_invalid_type_or_currency(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        existing_account_count = PlatformFinancialAccount.query.count()

        invalid_type = self.client.post(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
            json={
                "name": "Invalid Account",
                "account_type": "crypto",
                "currency_code": "KES",
            },
        )
        self.assertEqual(invalid_type.status_code, 400, invalid_type.json)

        invalid_currency = self.client.post(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
            json={
                "name": "Invalid Currency Account",
                "account_type": "bank",
                "currency_code": "ZZZ",
            },
        )
        self.assertEqual(invalid_currency.status_code, 400, invalid_currency.json)

        self.assertEqual(
            PlatformFinancialAccount.query.count(),
            existing_account_count,
        )

    def test_platform_financial_accounts_require_platform_owner_access(self):
        unauthenticated = self.client.get(
            "/api/v1/platform/owner/financial-accounts",
        )
        self.assertEqual(unauthenticated.status_code, 401)

        non_owner = self.client.get(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
        )
        self.assertEqual(non_owner.status_code, 403, non_owner.json)

        unauthenticated_create = self.client.post(
            "/api/v1/platform/owner/financial-accounts",
            json={
                "name": "Unauthorized Account",
                "account_type": "bank",
                "currency_code": "KES",
            },
        )
        self.assertEqual(unauthenticated_create.status_code, 401)

        non_owner_create = self.client.post(
            "/api/v1/platform/owner/financial-accounts",
            headers=self.headers,
            json={
                "name": "Unauthorized Account",
                "account_type": "bank",
                "currency_code": "KES",
            },
        )
        self.assertEqual(non_owner_create.status_code, 403, non_owner_create.json)


    def test_platform_owner_can_create_list_and_update_loan_products(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json={
                "name": "Starter Business Loan",
                "code": "STARTER-BIZ",
                "description": "Short-term business financing",
                "currency_code": "KES",
                "min_principal_minor": 100000,
                "max_principal_minor": 2000000,
                "interest_method": "reducing",
                "interest_rate_percent": "8.5",
                "repayment_frequency": "monthly",
                "min_term_count": 1,
                "max_term_count": 12,
                "origination_fee_minor": 5000,
                "late_payment_fee_minor": 1000,
                "allow_early_repayment": True,
            },
        )
        self.assertEqual(created.status_code, 201, created.json)
        self.assertTrue(created.json["success"])
        product = created.json["product"]
        product_id = product["id"]
        self.assertEqual(product["code"], "STARTER-BIZ")
        self.assertTrue(product["is_active"])

        listing = self.client.get(
            "/api/v1/platform/owner/products",
            headers=self.headers,
        )
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertIn(
            product_id,
            [item["id"] for item in listing.json["products"]],
        )

        updated = self.client.patch(
            f"/api/v1/platform/owner/products/{product_id}",
            headers=self.headers,
            json={
                "name": "Starter Business Loan Plus",
                "interest_rate_percent": "9.25",
                "is_active": False,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(
            updated.json["product"]["name"],
            "Starter Business Loan Plus",
        )
        self.assertEqual(
            updated.json["product"]["interest_rate_percent"],
            "9.250000",
        )
        self.assertFalse(updated.json["product"]["is_active"])

        inactive = self.client.get(
            "/api/v1/platform/owner/products?is_active=false",
            headers=self.headers,
        )
        self.assertEqual(inactive.status_code, 200, inactive.json)
        self.assertIn(
            product_id,
            [item["id"] for item in inactive.json["products"]],
        )

        customer_listing = self.client.get(
            "/api/v1/platform/products",
            headers=self.headers,
        )
        self.assertEqual(customer_listing.status_code, 200)
        self.assertNotIn(
            product_id,
            [item["id"] for item in customer_listing.json["products"]],
        )

    def test_platform_owner_product_creation_rejects_invalid_data(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        payload = {
            "name": "Invalid Product",
            "code": "INVALID-PRODUCT",
            "currency_code": "KES",
            "min_principal_minor": 100000,
            "max_principal_minor": 50000,
            "interest_method": "none",
            "interest_rate_percent": "5",
            "repayment_frequency": "monthly",
            "min_term_count": 1,
            "max_term_count": 12,
        }
        response = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(response.status_code, 400, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_owner_one_time_product_maturity_rules(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        base_payload = {
            "name": "One-Time Test Loan",
            "currency_code": "KES",
            "min_principal_minor": 100000,
            "max_principal_minor": 500000,
            "interest_method": "none",
            "interest_rate_percent": "0",
            "repayment_frequency": "one_time",
            "min_term_count": 1,
            "max_term_count": 1,
        }

        missing_maturity = dict(
            base_payload,
            code="ONE-TIME-NO-DAYS",
        )
        response = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json=missing_maturity,
        )
        self.assertEqual(response.status_code, 400, response.json)

        multiple_installments = dict(
            base_payload,
            code="ONE-TIME-MULTI",
            one_time_due_days=30,
            max_term_count=2,
        )
        response = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json=multiple_installments,
        )
        self.assertEqual(response.status_code, 400, response.json)

        valid_product = dict(
            base_payload,
            code="ONE-TIME-VALID",
            one_time_due_days=30,
        )
        response = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json=valid_product,
        )
        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(
            response.json["product"]["one_time_due_days"],
            30,
        )

    def test_non_owner_cannot_manage_platform_loan_products(self):
        product = self.create_platform_loan_product()
        db.session.commit()

        listing = self.client.get(
            "/api/v1/platform/owner/products",
            headers=self.headers,
        )
        self.assertEqual(listing.status_code, 403, listing.json)

        creation = self.client.post(
            "/api/v1/platform/owner/products",
            headers=self.headers,
            json={
                "name": "Unauthorized Product",
                "code": "UNAUTHORIZED",
                "currency_code": "KES",
                "min_principal_minor": 10000,
                "max_principal_minor": 50000,
                "interest_method": "none",
                "interest_rate_percent": 0,
                "repayment_frequency": "monthly",
                "min_term_count": 1,
                "max_term_count": 3,
            },
        )
        self.assertEqual(creation.status_code, 403, creation.json)

        update = self.client.patch(
            f"/api/v1/platform/owner/products/{product.id}",
            headers=self.headers,
            json={"is_active": False},
        )
        self.assertEqual(update.status_code, 403, update.json)
        self.assertTrue(product.is_active)


    def test_platform_owner_can_register_list_and_view_investor(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={
                "name": "Investor API Test",
                "email": "investor@example.invalid",
                "phone": "0712345678",
                "notes": "Initial investor record",
            },
        )
        self.assertEqual(created.status_code, 201, created.json)
        investor_id = created.json["investor"]["id"]

        listing = self.client.get(
            "/api/v1/platform/owner/investors?search=Investor%20API",
            headers=self.headers,
        )
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json["pagination"]["total"], 1)
        self.assertEqual(listing.json["investors"][0]["id"], investor_id)

        detail = self.client.get(
            f"/api/v1/platform/owner/investors/{investor_id}",
            headers=self.headers,
        )
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json["investor"]["name"], "Investor API Test")
        self.assertEqual(detail.json["capital"], [])
        self.assertEqual(detail.json["contributions"], [])
        self.assertEqual(detail.json["withdrawals"], [])

    def test_investor_available_capital_tracks_allocation_repayment_and_reversal(self):
        account = self.create_platform_accounting_setup()
        investor, _contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=150000,
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.flush()

        application = self.create_approved_platform_application(
            approved_principal_minor=100000,
            term_count=6,
        )
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        disburse_platform_loan(
            loan_id=loan.id,
            financial_account_id=account.id,
            customer_financial_account_id=self.source_account.id,
            amount_minor=100000,
            funding_source="external_investor",
            investor_allocations=[{
                "investor_id": str(investor.id),
                "amount_minor": 100000,
            }],
            disbursement_date=datetime(
                2026, 11, 16, 9, 0, tzinfo=timezone.utc
            ),
            recorded_by=self.user.id,
        )
        db.session.commit()

        detail_url = (
            f"/api/v1/platform/owner/investors/{investor.id}"
        )

        def available_capital():
            response = self.client.get(
                detail_url,
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 200, response.json)
            capital = next(
                item for item in response.json["capital"]
                if item["currency_code"] == "KES"
            )
            return capital["available_minor"]

        # KES 150,000 contributed; KES 100,000 allocated to the loan.
        self.assertEqual(available_capital(), 50000)

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=installment.interest_minor + 10000,
            payment_date=date(2026, 11, 17),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference="INV-AVAILABILITY-REPAY-001",
        )
        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.commit()

        # Principal repayment releases KES 10,000 to investor availability.
        self.assertEqual(available_capital(), 60000)

        reverse_platform_loan_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference="INV-AVAILABILITY-REV-001",
            transaction_date=datetime(
                2026, 11, 18, 9, 0, tzinfo=timezone.utc
            ),
        )
        db.session.commit()

        # Reversal locks the previously released KES 10,000 again.
        self.assertEqual(available_capital(), 50000)

        excessive_withdrawal = self.client.post(
            f"/api/v1/platform/owner/investors/{investor.id}/withdrawals",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 60000,
                "currency_code": "KES",
                "withdrawal_date": "2026-11-18T10:00:00+03:00",
                "reference": "INV-AVAILABILITY-EXCESS-001",
            },
        )
        self.assertEqual(
            excessive_withdrawal.status_code,
            409,
            excessive_withdrawal.json,
        )

        valid_withdrawal = self.client.post(
            f"/api/v1/platform/owner/investors/{investor.id}/withdrawals",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 50000,
                "currency_code": "KES",
                "withdrawal_date": "2026-11-18T10:05:00+03:00",
                "reference": "INV-AVAILABILITY-VALID-001",
            },
        )
        self.assertEqual(
            valid_withdrawal.status_code,
            201,
            valid_withdrawal.json,
        )
        self.assertEqual(available_capital(), 0)

    def test_investor_contribution_reversal_restores_balanced_accounting(self):
        account = self.create_platform_accounting_setup()
        investor, contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=100000,
        )

        reversed_record = reverse_platform_investor_contribution(
            contribution_id=contribution.id,
            reversed_by=self.user.id,
            reference="TEST-CONTRIB-REVERSAL",
        )

        db.session.flush()
        reversal = PlatformJournalEntry.query.filter_by(
            reversal_of_id=contribution.journal_entry_id
        ).one()

        self.assertEqual(reversed_record.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(
            db.session.get(
                PlatformJournalEntry,
                contribution.journal_entry_id,
            ).status,
            "reversed",
        )

        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=reversal.id
        ).all()
        self.assertEqual(sum(line.debit_minor for line in lines), 100000)
        self.assertEqual(sum(line.credit_minor for line in lines), 100000)

        with self.assertRaises(PlatformLendingValidationError):
            reverse_platform_investor_contribution(
                contribution_id=contribution.id,
                reversed_by=self.user.id,
                reference="TEST-CONTRIB-REVERSAL-AGAIN",
            )

    def test_investor_contribution_reversal_rejects_withdrawn_capital(self):
        account = self.create_platform_accounting_setup()
        investor, contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=100000,
        )

        withdrawal_entry = create_platform_draft_entry(
            created_by=self.user.id,
            reference=f"TEST-WITHDRAW-{uuid4().hex[:12]}",
            description="Test investor withdrawal",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": self.platform_investor_capital_ledger.id,
                    "debit_minor": 40000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": self.platform_cash_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": 40000,
                },
            ],
        )
        post_platform_entry(
            entry_id=withdrawal_entry.id,
            posted_by=self.user.id,
        )
        db.session.add(PlatformInvestorWithdrawal(
            investor_id=investor.id,
            financial_account_id=account.id,
            journal_entry_id=withdrawal_entry.id,
            currency_code="KES",
            amount_minor=40000,
            withdrawal_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
            status="posted",
        ))
        db.session.flush()

        with self.assertRaises(PlatformLendingValidationError):
            reverse_platform_investor_contribution(
                contribution_id=contribution.id,
                reversed_by=self.user.id,
                reference="TEST-CONTRIB-REJECT",
            )

        self.assertEqual(contribution.status, "posted")

    def test_investor_withdrawal_reversal_restores_available_capital(self):
        account = self.create_platform_accounting_setup()
        investor, contribution = self.create_platform_investor_with_capital(
            account,
            amount_minor=100000,
        )

        withdrawal = record_platform_investor_withdrawal(
            investor_id=investor.id,
            financial_account_id=account.id,
            amount_minor=40000,
            currency_code="KES",
            withdrawal_date=datetime.now(timezone.utc),
            recorded_by=self.user.id,
            reference="TEST-WITHDRAWAL",
        )
        db.session.flush()

        reversed_record = reverse_platform_investor_withdrawal(
            withdrawal_id=withdrawal.id,
            reversed_by=self.user.id,
            reference="TEST-WITHDRAWAL-REVERSAL",
        )
        db.session.flush()

        reversal = PlatformJournalEntry.query.filter_by(
            reversal_of_id=withdrawal.journal_entry_id
        ).one()

        self.assertEqual(reversed_record.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(
            db.session.get(
                PlatformJournalEntry,
                withdrawal.journal_entry_id,
            ).status,
            "reversed",
        )

        with self.assertRaises(PlatformLendingValidationError):
            reverse_platform_investor_withdrawal(
                withdrawal_id=withdrawal.id,
                reversed_by=self.user.id,
                reference="TEST-WITHDRAWAL-REVERSAL-AGAIN",
            )

    def test_platform_owner_can_record_investor_contribution_and_withdrawal(self):
        account = self.create_platform_accounting_setup()
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={"name": "Capital Flow Investor"},
        )
        self.assertEqual(created.status_code, 201, created.json)
        investor_id = created.json["investor"]["id"]

        contribution = self.client.post(
            f"/api/v1/platform/owner/investors/{investor_id}/contributions",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 100000,
                "currency_code": "KES",
                "contribution_date": "2026-11-17T08:00:00+03:00",
                "reference": "INV-CONTRIB-001",
            },
        )
        self.assertEqual(contribution.status_code, 201, contribution.json)
        self.assertEqual(
            contribution.json["contribution"]["amount_minor"],
            100000,
        )

        contribution_entry = db.session.get(
            PlatformJournalEntry,
            UUID(contribution.json["contribution"]["journal_entry_id"]),
        )
        contribution_lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=contribution_entry.id
        ).all()
        self.assertEqual(contribution_entry.status, "posted")
        self.assertEqual(sum(x.debit_minor for x in contribution_lines), 100000)
        self.assertEqual(sum(x.credit_minor for x in contribution_lines), 100000)

        withdrawal = self.client.post(
            f"/api/v1/platform/owner/investors/{investor_id}/withdrawals",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 40000,
                "currency_code": "KES",
                "withdrawal_date": "2026-11-17T08:10:00+03:00",
                "reference": "INV-WITHDRAW-001",
            },
        )
        self.assertEqual(withdrawal.status_code, 201, withdrawal.json)
        self.assertEqual(
            withdrawal.json["withdrawal"]["amount_minor"],
            40000,
        )

        detail = self.client.get(
            f"/api/v1/platform/owner/investors/{investor_id}",
            headers=self.headers,
        )
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(len(detail.json["contributions"]), 1)
        self.assertEqual(len(detail.json["withdrawals"]), 1)
        self.assertEqual(detail.json["capital"][0]["contributed_minor"], 100000)
        self.assertEqual(detail.json["capital"][0]["withdrawn_minor"], 40000)
        self.assertEqual(detail.json["capital"][0]["available_minor"], 60000)

    def test_platform_owner_can_reverse_investor_contribution_and_withdrawal(self):
        account = self.create_platform_accounting_setup()
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={"name": "Reversal API Investor"},
        )
        self.assertEqual(created.status_code, 201, created.json)
        investor_id = created.json["investor"]["id"]

        contribution = self.client.post(
            f"/api/v1/platform/owner/investors/{investor_id}/contributions",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 100000,
                "currency_code": "KES",
                "contribution_date": "2026-11-17T08:00:00+03:00",
                "reference": "API-CONTRIB-REV",
            },
        )
        self.assertEqual(contribution.status_code, 201, contribution.json)
        contribution_id = contribution.json["contribution"]["id"]
        contribution_journal_id = contribution.json["contribution"]["journal_entry_id"]

        withdrawal = self.client.post(
            f"/api/v1/platform/owner/investors/{investor_id}/withdrawals",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 40000,
                "currency_code": "KES",
                "withdrawal_date": "2026-11-17T08:10:00+03:00",
                "reference": "API-WITHDRAW-REV",
            },
        )
        self.assertEqual(withdrawal.status_code, 201, withdrawal.json)
        withdrawal_id = withdrawal.json["withdrawal"]["id"]
        withdrawal_journal_id = withdrawal.json["withdrawal"]["journal_entry_id"]

        withdrawal_reversal = self.client.post(
            f"/api/v1/platform/owner/investors/withdrawals/{withdrawal_id}/reverse",
            headers=self.headers,
            json={"reference": "API-WITHDRAW-REVERSAL"},
        )
        self.assertEqual(withdrawal_reversal.status_code, 200, withdrawal_reversal.json)
        self.assertEqual(withdrawal_reversal.json["withdrawal"]["status"], "reversed")

        contribution_reversal = self.client.post(
            f"/api/v1/platform/owner/investors/contributions/{contribution_id}/reverse",
            headers=self.headers,
            json={
                "reference": "API-CONTRIB-REVERSAL",
                "transaction_date": "2026-11-17T09:00:00+03:00",
            },
        )
        self.assertEqual(contribution_reversal.status_code, 200, contribution_reversal.json)
        self.assertEqual(contribution_reversal.json["contribution"]["status"], "reversed")

        self.assertEqual(
            db.session.get(PlatformJournalEntry, UUID(withdrawal_journal_id)).status,
            "reversed",
        )
        self.assertEqual(
            db.session.get(PlatformJournalEntry, UUID(contribution_journal_id)).status,
            "reversed",
        )

        detail = self.client.get(
            f"/api/v1/platform/owner/investors/{investor_id}",
            headers=self.headers,
        )
        self.assertEqual(detail.status_code, 200, detail.json)
        self.assertEqual(detail.json["capital"][0]["available_minor"], 0)

    def test_investor_contribution_converts_accounting_error_and_rolls_back(self):
        account = self.create_platform_accounting_setup()
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={"name": "Accounting Error Investor"},
        )
        self.assertEqual(created.status_code, 201, created.json)
        investor_id = created.json["investor"]["id"]

        journal_count = PlatformJournalEntry.query.count()

        with patch(
            "app.services.platform_lending.create_draft_entry",
            side_effect=PlatformAccountingError("Simulated accounting failure"),
        ):
            response = self.client.post(
                f"/api/v1/platform/owner/investors/{investor_id}/contributions",
                headers=self.headers,
                json={
                    "financial_account_id": str(account.id),
                    "amount_minor": 50000,
                    "currency_code": "KES",
                    "contribution_date": "2026-11-17T08:20:00+03:00",
                },
            )

        self.assertEqual(response.status_code, 409, response.json)
        self.assertFalse(response.json["success"])
        self.assertIn("Simulated accounting failure", response.json["message"])
        self.assertEqual(
            PlatformInvestorContribution.query.filter_by(
                investor_id=UUID(investor_id)
            ).count(),
            0,
        )
        self.assertEqual(PlatformJournalEntry.query.count(), journal_count)

    def test_platform_investor_withdrawal_rejects_insufficient_available_capital(self):
        account = self.create_platform_accounting_setup()
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        created = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={"name": "Insufficient Capital Investor"},
        )
        self.assertEqual(created.status_code, 201, created.json)
        investor_id = created.json["investor"]["id"]

        response = self.client.post(
            f"/api/v1/platform/owner/investors/{investor_id}/withdrawals",
            headers=self.headers,
            json={
                "financial_account_id": str(account.id),
                "amount_minor": 1,
                "currency_code": "KES",
                "withdrawal_date": "2026-11-17T08:15:00+03:00",
            },
        )
        self.assertEqual(response.status_code, 409, response.json)
        self.assertFalse(response.json["success"])
        self.assertEqual(
            PlatformInvestorWithdrawal.query.filter_by(
                investor_id=UUID(investor_id)
            ).count(),
            0,
        )

    def test_customer_cannot_access_platform_investor_management(self):
        listing = self.client.get(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
        )
        self.assertEqual(listing.status_code, 403)

        creation = self.client.post(
            "/api/v1/platform/owner/investors",
            headers=self.headers,
            json={"name": "Unauthorized Investor"},
        )
        self.assertEqual(creation.status_code, 403)


    def test_customer_cannot_access_platform_owner_applications(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)

        submitted = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )
        self.assertEqual(submitted.status_code, 201, submitted.json)

        response = self.client.get(
            "/api/v1/platform/owner/applications",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_owner_can_retrieve_platform_loan_details(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            f"/api/v1/platform/owner/loans/{loan.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        detail = response.json["loan"]
        self.assertEqual(detail["id"], str(loan.id))
        self.assertEqual(detail["customer_email"], self.user.email)
        self.assertEqual(detail["product_name"], loan.product.name if hasattr(loan, "product") else detail["product_name"])
        self.assertEqual(detail["approved_principal_minor"], 100000)
        self.assertEqual(detail["disbursed_principal_minor"], 100000)
        self.assertEqual(detail["outstanding_principal_minor"], 100000)

        self.assertEqual(len(response.json["installments"]), 6)
        self.assertEqual(len(response.json["disbursements"]), 1)
        self.assertEqual(response.json["payments"], [])

        summary = response.json["summary"]
        self.assertEqual(summary["total_disbursed_minor"], 100000)
        self.assertEqual(summary["outstanding_principal_minor"], 100000)
        self.assertEqual(summary["confirmed_repayments_minor"], 0)
        self.assertEqual(summary["pending_repayments_minor"], 0)

    def test_platform_owner_loan_details_reject_invalid_or_missing_ids(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        invalid = self.client.get(
            "/api/v1/platform/owner/loans/not-a-uuid",
            headers=self.headers,
        )
        self.assertEqual(invalid.status_code, 400)

        missing = self.client.get(
            "/api/v1/platform/owner/loans/00000000-0000-0000-0000-000000000000",
            headers=self.headers,
        )
        self.assertEqual(missing.status_code, 404)

    def test_customer_cannot_access_platform_owner_loan_details(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        response = self.client.get(
            f"/api/v1/platform/owner/loans/{loan.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])


    def test_platform_owner_can_list_and_filter_loans(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/loans?status=active&page=1&per_page=10",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertGreaterEqual(response.json["pagination"]["total"], 1)
        self.assertEqual(
            response.json["pagination"]["total"],
            len(response.json["loans"]),
        )
        self.assertEqual(response.json["pagination"]["page"], 1)

        item = next(
            entry for entry in response.json["loans"]
            if entry["id"] == str(loan.id)
        )
        self.assertEqual(item["customer_id"], str(self.user.id))
        self.assertEqual(item["customer_email"], self.user.email)
        self.assertEqual(item["approved_principal_minor"], 100000)
        self.assertEqual(item["disbursed_principal_minor"], 100000)
        self.assertEqual(item["outstanding_principal_minor"], 100000)
        self.assertEqual(item["currency_code"], "KES")
        self.assertEqual(item["status"], "active")

    def test_customer_cannot_access_platform_owner_loans(self):
        response = self.client.get(
            "/api/v1/platform/owner/loans",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_owner_loan_listing_rejects_invalid_filters(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        invalid_status = self.client.get(
            "/api/v1/platform/owner/loans?status=unknown",
            headers=self.headers,
        )
        self.assertEqual(invalid_status.status_code, 400)

        invalid_page = self.client.get(
            "/api/v1/platform/owner/loans?page=0",
            headers=self.headers,
        )
        self.assertEqual(invalid_page.status_code, 400)


    def test_platform_owner_can_view_portfolio_report(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        first_installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(
            PlatformLoanInstallment.installment_number
        ).first()
        first_installment.due_date = date.today() - timedelta(days=1)

        record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"REPORT-{uuid4().hex[:12]}",
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/reports/portfolio",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        report = next(
            item for item in response.json["currencies"]
            if item["currency_code"] == "KES"
        )

        self.assertGreaterEqual(report["loan_count"], 1)
        self.assertGreaterEqual(
            report["approved_principal_minor"], 100000
        )
        self.assertGreaterEqual(
            report["disbursed_principal_minor"], 100000
        )
        self.assertGreaterEqual(
            report["outstanding_principal_minor"], 100000
        )
        self.assertGreaterEqual(
            report["pending_repayments_minor"], 1000
        )
        self.assertGreaterEqual(
            report["overdue_installment_count"], 1
        )
        self.assertGreaterEqual(
            report["overdue_loan_count"], 1
        )
        self.assertGreaterEqual(
            report["funding_sources"]["pesapilot_capital"], 100000
        )

    def test_customer_cannot_access_platform_portfolio_report(self):
        response = self.client.get(
            "/api/v1/platform/owner/reports/portfolio",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])


    def test_platform_loan_snapshots_product_late_payment_fee(self):
        product = self.create_platform_loan_product(
            late_payment_fee_minor=375
        )
        application = self.create_approved_platform_application(
            product=product
        )

        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        self.assertEqual(loan.late_payment_fee_minor, 375)

    def test_platform_late_fee_assessment_payment_and_reversal(self):
        loan, account = self.create_funded_platform_loan_for_repayment()
        self.create_platform_late_fee_ledgers()
        loan.late_payment_fee_minor = 2500

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(
            PlatformLoanInstallment.installment_number
        ).first()
        installment.due_date = date(2026, 11, 21)
        as_of_date = date(2026, 11, 22)

        assessed = assess_overdue_platform_loan_fees(
            as_of_date=as_of_date,
            assessed_by=self.user.id,
        )
        self.assertEqual(assessed, 1)
        db.session.flush()

        fee = PlatformLoanLateFee.query.filter_by(
            installment_id=installment.id
        ).one()
        self.assertEqual(fee.amount_minor, 2500)
        self.assertEqual(fee.paid_minor, 0)
        self.assertEqual(fee.status, "assessed")
        self.assertIsNotNone(fee.journal_entry_id)

        # Re-running assessment must not charge the same installment twice.
        self.assertEqual(
            assess_overdue_platform_loan_fees(
                as_of_date=as_of_date,
                assessed_by=self.user.id,
            ),
            0,
        )
        self.assertEqual(
            PlatformLoanLateFee.query.filter_by(
                installment_id=installment.id
            ).count(),
            1,
        )

        payment = record_platform_late_fee_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=as_of_date,
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"FEE-PAY-{uuid4().hex[:12]}",
        )
        self.assertEqual(payment.status, "pending")

        confirmed = confirm_platform_late_fee_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        self.assertEqual(confirmed.status, "confirmed")
        self.assertIsNotNone(confirmed.journal_entry_id)
        self.assertEqual(fee.paid_minor, 1000)
        self.assertEqual(fee.status, "partially_paid")

        allocation = PlatformLoanLateFeePaymentAllocation.query.filter_by(
            payment_id=payment.id
        ).one()
        self.assertEqual(allocation.late_fee_id, fee.id)
        self.assertEqual(allocation.amount_minor, 1000)

        reversal = reverse_platform_late_fee_payment(
            payment_id=payment.id,
            reversed_by=self.user.id,
            reference=f"FEE-REV-{uuid4().hex[:12]}",
            transaction_date=datetime(
                2026, 11, 22, 12, 0, tzinfo=timezone.utc
            ),
        )
        self.assertIsNotNone(reversal)
        self.assertEqual(payment.status, "reversed")
        self.assertEqual(fee.paid_minor, 0)
        self.assertEqual(fee.status, "assessed")

        rejected_payment = record_platform_late_fee_payment(
            loan_id=loan.id,
            amount_minor=500,
            payment_date=as_of_date,
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"FEE-REJ-{uuid4().hex[:12]}",
        )
        rejected = reject_platform_late_fee_payment(
            payment_id=rejected_payment.id,
            rejected_by=self.user.id,
            reason="Payment reference could not be verified.",
        )
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(fee.paid_minor, 0)
        self.assertEqual(
            PlatformLoanLateFeePaymentAllocation.query.filter_by(
                payment_id=rejected_payment.id
            ).count(),
            0,
        )

    def test_platform_owner_can_view_collections_queue(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        first_installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(
            PlatformLoanInstallment.installment_number
        ).first()
        first_installment.due_date = date.today() - timedelta(days=1)

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"COLLECTION-{uuid4().hex[:12]}",
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/collections",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        pending = next(
            item for item in response.json["pending_payments"]
            if item["id"] == str(payment.id)
        )
        self.assertEqual(pending["loan_id"], str(loan.id))
        self.assertEqual(pending["amount_minor"], 1000)
        self.assertEqual(pending["currency_code"], "KES")

        overdue = next(
            item for item in response.json["overdue_installments"]
            if item["id"] == str(first_installment.id)
        )
        self.assertEqual(overdue["loan_id"], str(loan.id))
        self.assertEqual(
            overdue["remaining_minor"],
            first_installment.amount_minor - first_installment.paid_minor,
        )

        invalid_page = self.client.get(
            "/api/v1/platform/owner/collections?page=0",
            headers=self.headers,
        )
        self.assertEqual(invalid_page.status_code, 400)

    def test_platform_owner_collections_include_pending_and_confirmed_fee_payments(self):
        loan, account = self.create_funded_platform_loan_for_repayment()
        self.create_platform_late_fee_ledgers()
        loan.late_payment_fee_minor = 2500

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(
            PlatformLoanInstallment.installment_number
        ).first()
        installment.due_date = date.today() - timedelta(days=1)

        assessed = assess_overdue_platform_loan_fees(
            as_of_date=date.today(),
            assessed_by=self.user.id,
        )
        self.assertEqual(assessed, 1)

        confirmed_payment = record_platform_late_fee_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"FEE-CONF-{uuid4().hex[:12]}",
        )
        confirm_platform_late_fee_payment(
            payment_id=confirmed_payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )

        pending_payment = record_platform_late_fee_payment(
            loan_id=loan.id,
            amount_minor=500,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"FEE-PEND-{uuid4().hex[:12]}",
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/collections",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        pending = response.json["pending_late_fee_payments"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], str(pending_payment.id))
        self.assertEqual(pending[0]["loan_id"], str(loan.id))
        self.assertEqual(pending[0]["amount_minor"], 500)

        confirmed = response.json["confirmed_late_fee_payments"]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["id"], str(confirmed_payment.id))
        self.assertEqual(confirmed[0]["loan_id"], str(loan.id))
        self.assertEqual(confirmed[0]["amount_minor"], 1000)
        self.assertIsNotNone(confirmed[0]["confirmed_at"])

        pagination = response.json["pagination"]
        self.assertEqual(pagination["pending_late_fee_payments_total"], 1)
        self.assertEqual(pagination["confirmed_late_fee_payments_total"], 1)

    def test_customer_cannot_access_platform_collections_queue(self):
        response = self.client.get(
            "/api/v1/platform/owner/collections",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_customer_can_list_only_their_platform_loans(self):
        own_loan, _account = self.create_funded_platform_loan_for_repayment()

        another_customer = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(another_customer)
        db.session.flush()

        other_loan, _other_account = self.create_funded_platform_loan_for_repayment()
        other_loan.customer_id = another_customer.id
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/loans",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        loan_ids = [item["id"] for item in response.json["loans"]]
        self.assertIn(str(own_loan.id), loan_ids)
        self.assertNotIn(str(other_loan.id), loan_ids)

    def test_customer_can_view_platform_loan_detail_and_repayment_history(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        payment_response = self.client.post(
            f"/api/v1/platform/loans/{loan.id}/payments",
            headers=self.headers,
            json={
                "amount_minor": 1000,
                "payment_date": date.today().isoformat(),
                "payment_method": "mobile_money",
                "reference": f"DETAIL-{uuid4().hex[:12]}",
            },
        )
        self.assertEqual(payment_response.status_code, 201, payment_response.json)

        response = self.client.get(
            f"/api/v1/platform/loans/{loan.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        data = response.json["loan"]
        self.assertEqual(data["id"], str(loan.id))
        self.assertTrue(response.json["installments"])
        self.assertEqual(len(response.json["payments"]), 1)
        self.assertEqual(response.json["payments"][0]["status"], "pending")
        self.assertEqual(response.json["payments"][0]["amount_minor"], 1000)

        for forbidden_key in (
            "journal_entry_id",
            "financial_account_id",
            "funding_source",
            "recorded_by",
            "confirmed_by",
        ):
            self.assertNotIn(forbidden_key, data)
            self.assertNotIn(forbidden_key, response.json["payments"][0])

    def test_customer_cannot_view_another_customers_platform_loan(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        another_customer = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(another_customer)
        db.session.flush()
        loan.customer_id = another_customer.id
        db.session.commit()

        response = self.client.get(
            f"/api/v1/platform/loans/{loan.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404, response.json)
        self.assertFalse(response.json["success"])

    def test_customer_can_submit_platform_loan_repayment(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()
        original_outstanding = loan.outstanding_principal_minor

        response = self.client.post(
            f"/api/v1/platform/loans/{loan.id}/payments",
            headers=self.headers,
            json={
                "amount_minor": 1000,
                "payment_date": date.today().isoformat(),
                "payment_method": "mobile_money",
                "reference": f"CUSTOMER-{uuid4().hex[:12]}",
                "notes": "Customer repayment report",
            },
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])
        payment_data = response.json["payment"]
        self.assertEqual(payment_data["loan_id"], str(loan.id))
        self.assertEqual(payment_data["amount_minor"], 1000)
        self.assertEqual(payment_data["status"], "pending")
        self.assertEqual(payment_data["payment_method"], "mobile_money")

        payment = db.session.get(
            PlatformLoanPayment,
            UUID(payment_data["id"]),
        )
        db.session.refresh(loan)
        self.assertEqual(payment.recorded_by, self.user.id)
        self.assertIsNone(payment.financial_account_id)
        self.assertIsNone(payment.journal_entry_id)
        self.assertEqual(loan.outstanding_principal_minor, original_outstanding)
        self.assertEqual(
            PlatformLoanPaymentAllocation.query.filter_by(
                payment_id=payment.id
            ).count(),
            0,
        )

    def test_customer_repayment_rejects_invalid_payment_method(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        response = self.client.post(
            f"/api/v1/platform/loans/{loan.id}/payments",
            headers=self.headers,
            json={
                "amount_minor": 1000,
                "payment_date": date.today().isoformat(),
                "payment_method": ["mobile_money"],
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertFalse(response.json["success"])
        self.assertEqual(
            PlatformLoanPayment.query.filter_by(loan_id=loan.id).count(),
            0,
        )

    def test_customer_cannot_submit_repayment_for_another_customers_loan(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()
        another_customer = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(another_customer)
        db.session.flush()

        loan.customer_id = another_customer.id
        db.session.flush()

        response = self.client.post(
            f"/api/v1/platform/loans/{loan.id}/payments",
            headers=self.headers,
            json={
                "amount_minor": 1000,
                "payment_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )

        self.assertEqual(response.status_code, 404, response.json)
        self.assertFalse(response.json["success"])
        self.assertEqual(
            PlatformLoanPayment.query.filter_by(loan_id=loan.id).count(),
            0,
        )


    def test_platform_owner_can_reject_pending_platform_loan_repayment(self):
        loan, _account = self.create_funded_platform_loan_for_repayment()

        installments = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id
        ).order_by(PlatformLoanInstallment.installment_number).all()

        original_outstanding = loan.outstanding_principal_minor
        original_paid = [item.paid_minor for item in installments]
        original_journal_count = PlatformJournalEntry.query.count()

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"OWNER-REJECT-{uuid4().hex[:12]}",
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/collections/payments/{payment.id}/reject",
            headers=self.headers,
            json={"reason": "Test repayment report; no funds received."},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        payment_data = response.json["payment"]
        self.assertEqual(payment_data["id"], str(payment.id))
        self.assertEqual(payment_data["loan_id"], str(loan.id))
        self.assertEqual(payment_data["status"], "rejected")
        self.assertEqual(
            payment_data["rejected_by"],
            str(self.user.id),
        )
        self.assertIsNotNone(payment_data["rejected_at"])
        self.assertEqual(
            payment_data["rejection_reason"],
            "Test repayment report; no funds received.",
        )

        db.session.refresh(payment)
        db.session.refresh(loan)

        self.assertEqual(payment.status, "rejected")
        self.assertEqual(payment.rejected_by, self.user.id)
        self.assertIsNotNone(payment.rejected_at)
        self.assertEqual(
            payment.rejection_reason,
            "Test repayment report; no funds received.",
        )

        self.assertIsNone(payment.financial_account_id)
        self.assertIsNone(payment.journal_entry_id)
        self.assertIsNone(payment.confirmed_by)
        self.assertIsNone(payment.confirmed_at)
        self.assertEqual(payment.principal_minor, 0)
        self.assertEqual(payment.interest_minor, 0)

        self.assertEqual(
            loan.outstanding_principal_minor,
            original_outstanding,
        )

        for installment in installments:
            db.session.refresh(installment)

        self.assertEqual(
            [item.paid_minor for item in installments],
            original_paid,
        )

        self.assertEqual(
            PlatformLoanPaymentAllocation.query.filter_by(
                payment_id=payment.id
            ).count(),
            0,
        )
        self.assertEqual(
            PlatformJournalEntry.query.count(),
            original_journal_count,
        )

    def test_customer_cannot_reject_platform_loan_repayment(self):
        response = self.client.post(
            f"/api/v1/platform/owner/collections/payments/{uuid4()}/reject",
            headers=self.headers,
            json={"reason": "Test rejection"},
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_owner_can_confirm_platform_loan_repayment(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=1000,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"OWNER-CONFIRM-{uuid4().hex[:12]}",
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/collections/payments/{payment.id}/confirm",
            headers=self.headers,
            json={"financial_account_id": str(account.id)},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        payment_data = response.json["payment"]
        self.assertEqual(payment_data["id"], str(payment.id))
        self.assertEqual(payment_data["loan_id"], str(loan.id))
        self.assertEqual(payment_data["status"], "confirmed")
        self.assertEqual(
            payment_data["financial_account_id"],
            str(account.id),
        )
        self.assertEqual(payment_data["amount_minor"], 1000)
        self.assertIsNotNone(payment_data["journal_entry_id"])

        db.session.refresh(payment)
        self.assertEqual(payment.status, "confirmed")
        self.assertIsNotNone(payment.journal_entry_id)

        journal = db.session.get(
            PlatformJournalEntry,
            payment.journal_entry_id,
        )
        self.assertIsNotNone(journal)
        self.assertEqual(journal.status, "posted")

    def test_customer_cannot_confirm_platform_loan_repayment(self):
        response = self.client.post(
            "/api/v1/platform/owner/collections/payments/"
            f"{uuid4()}/confirm",
            headers=self.headers,
            json={"financial_account_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_owner_can_reverse_platform_loan_repayment(self):
        loan, account = self.create_funded_platform_loan_for_repayment()

        installment = PlatformLoanInstallment.query.filter_by(
            loan_id=loan.id,
            installment_number=1,
        ).one()

        payment_amount = installment.interest_minor + 100
        original_outstanding = loan.outstanding_principal_minor
        original_paid = installment.paid_minor

        payment = record_platform_loan_payment(
            loan_id=loan.id,
            amount_minor=payment_amount,
            payment_date=date.today(),
            payment_method="mobile_money",
            recorded_by=self.user.id,
            reference=f"OWNER-REVERSAL-{uuid4().hex[:12]}",
        )

        confirm_platform_loan_payment(
            payment_id=payment.id,
            financial_account_id=account.id,
            confirmed_by=self.user.id,
        )
        db.session.flush()

        original_journal_id = payment.journal_entry_id

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/collections/payments/{payment.id}/reverse",
            headers=self.headers,
            json={"reference": f"OWNER-REV-{uuid4().hex[:12]}"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["payment"]["status"], "reversed")
        self.assertIsNotNone(
            response.json["reversal"]["journal_entry_id"]
        )
        self.assertEqual(
            response.json["reversal"]["status"],
            "posted",
        )

        db.session.refresh(payment)
        db.session.refresh(loan)
        db.session.refresh(installment)

        self.assertEqual(payment.status, "reversed")
        self.assertEqual(installment.paid_minor, original_paid)
        self.assertEqual(
            loan.outstanding_principal_minor,
            original_outstanding,
        )

        original_journal = db.session.get(
            PlatformJournalEntry,
            original_journal_id,
        )
        self.assertEqual(original_journal.status, "reversed")

        reversal_journal = db.session.get(
            PlatformJournalEntry,
            UUID(response.json["reversal"]["journal_entry_id"]),
        )
        self.assertIsNotNone(reversal_journal)
        self.assertEqual(reversal_journal.status, "posted")
        self.assertEqual(
            reversal_journal.reversal_of_id,
            original_journal_id,
        )

    def test_customer_cannot_reverse_platform_loan_repayment(self):
        response = self.client.post(
            "/api/v1/platform/owner/collections/payments/"
            f"{uuid4()}/reverse",
            headers=self.headers,
            json={"reference": "CUSTOMER-REVERSAL"},
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertFalse(response.json["success"])

    def test_platform_loan_reversal_rejects_missing_reference(self):
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/collections/payments/{uuid4()}/reverse",
            headers=self.headers,
            json={},
        )

        self.assertEqual(response.status_code, 400, response.json)

    def test_platform_owner_can_list_and_filter_applications(self):
        product = self.create_platform_loan_product()

        baseline_total = PlatformLoanApplication.query.count()
        baseline_withdrawn = PlatformLoanApplication.query.filter_by(
            status="withdrawn"
        ).count()

        pending = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Pending application",
            status="pending",
        )
        withdrawn = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=150000,
            term_count=5,
            purpose="Withdrawn application",
            status="withdrawn",
        )
        db.session.add_all([pending, withdrawn])
        db.session.flush()

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/applications",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(
            response.json["pagination"]["total"],
            baseline_total + 2,
        )
        self.assertEqual(response.json["pagination"]["page"], 1)
        self.assertEqual(response.json["pagination"]["per_page"], 20)

        own_pending = next(
            item for item in response.json["applications"]
            if item["id"] == str(pending.id)
        )
        self.assertEqual(own_pending["customer_email"], self.user.email)
        self.assertEqual(own_pending["product_name"], product.name)
        self.assertEqual(own_pending["currency_code"], "KES")
        self.assertIsNone(own_pending["applicant"])
        self.assertIsNone(own_pending["affordability_assessment"])

        filtered = self.client.get(
            "/api/v1/platform/owner/applications?status=withdrawn",
            headers=self.headers,
        )

        self.assertEqual(filtered.status_code, 200, filtered.json)
        self.assertEqual(
            filtered.json["pagination"]["total"],
            baseline_withdrawn + 1,
        )
        self.assertTrue(
            any(
                item["id"] == str(withdrawn.id)
                and item["status"] == "withdrawn"
                for item in filtered.json["applications"]
            )
        )

        invalid = self.client.get(
            "/api/v1/platform/owner/applications?status=unknown",
            headers=self.headers,
        )
        self.assertEqual(invalid.status_code, 400, invalid.json)

    def test_platform_owner_can_view_application_details_and_affordability_without_national_id(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        submitted = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product, monthly_income_minor=600000,
                monthly_expenses_minor=0,
            ),
        )
        self.assertEqual(submitted.status_code, 201, submitted.json)

        application_id = submitted.json["application"]["id"]
        application = db.session.get(
            PlatformLoanApplication,
            UUID(application_id),
        )
        details = db.session.get(
            PlatformLoanApplicationDetails,
            application.id,
        )
        self.assertIsNotNone(details)

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.get(
            "/api/v1/platform/owner/applications",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        item = next(
            row for row in response.json["applications"]
            if row["id"] == application_id
        )

        applicant = item["applicant"]
        self.assertEqual(applicant["full_name"], "Test Applicant")
        self.assertEqual(applicant["phone"], "+254700000000")
        self.assertEqual(applicant["email"], self.user.email)
        self.assertEqual(applicant["residential_address"], "Nairobi, Kenya")
        self.assertEqual(applicant["employment_status"], "employed")
        self.assertEqual(applicant["employer_business_name"], "Test Employer")
        self.assertEqual(applicant["monthly_income_minor"], 600000)
        self.assertEqual(applicant["monthly_expenses_minor"], 0)
        self.assertEqual(applicant["existing_monthly_repayments_minor"], 0)
        self.assertTrue(applicant["affordability_consent"])

        assessment = item["affordability_assessment"]
        self.assertIsNotNone(assessment)
        self.assertEqual(assessment["version"], 1)
        self.assertEqual(assessment["status"], "verified")
        self.assertEqual(
            assessment["recommended_monthly_instalment_minor"], 210000
        )

        self.assertNotIn("national_id", applicant)
        self.assertNotIn("national_id_encrypted", applicant)
        self.assertNotIn("national_id", item)
        self.assertNotIn("national_id_encrypted", item)
        self.assertNotIn("12345678", str(item))
        self.assertNotIn(details.national_id_encrypted, str(item))


    def test_platform_owner_can_create_loan_from_approved_application(self):
        application = self.create_approved_platform_application(
            requested_principal_minor=100000,
            approved_principal_minor=75000,
        )
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()
        initial_loan_count = PlatformLoan.query.count()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/create-loan",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])
        loan_data = response.json["loan"]
        self.assertEqual(loan_data["application_id"], str(application.id))
        self.assertEqual(loan_data["approved_principal_minor"], 75000)
        self.assertEqual(loan_data["principal_minor"], 0)
        self.assertEqual(loan_data["outstanding_principal_minor"], 0)
        self.assertEqual(loan_data["status"], "created")
        self.assertEqual(PlatformLoan.query.count(), initial_loan_count + 1)

    def test_platform_owner_cannot_create_duplicate_platform_loan(self):
        application = self.create_approved_platform_application()
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()
        initial_loan_count = PlatformLoan.query.count()

        url = (
            f"/api/v1/platform/owner/applications/"
            f"{application.id}/create-loan"
        )
        first = self.client.post(url, headers=self.headers)
        self.assertEqual(first.status_code, 201, first.json)

        second = self.client.post(url, headers=self.headers)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(PlatformLoan.query.count(), initial_loan_count + 1)

    def test_platform_owner_cannot_create_loan_from_pending_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()
        initial_loan_count = PlatformLoan.query.count()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/create-loan",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(PlatformLoan.query.count(), initial_loan_count)

    def test_customer_cannot_create_platform_loan(self):
        application = self.create_approved_platform_application()
        initial_loan_count = PlatformLoan.query.count()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/create-loan",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(PlatformLoan.query.count(), initial_loan_count)

    def test_platform_owner_can_fully_disburse_platform_loan(self):
        financial_account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "financial_account_id": str(financial_account.id),
                "customer_financial_account_id": str(self.source_account.id),
                "amount_minor": 100000,
                "funding_source": "pesapilot_capital",
                "disbursement_date": "2026-11-16T10:00:00+03:00",
            },
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        disbursement_data = response.json["disbursement"]
        loan_data = response.json["loan"]

        self.assertEqual(disbursement_data["loan_id"], str(loan.id))
        self.assertEqual(disbursement_data["financial_account_id"],
                         str(financial_account.id))
        self.assertEqual(disbursement_data["amount_minor"], 100000)
        self.assertEqual(disbursement_data["funding_source"],
                         "pesapilot_capital")

        self.assertEqual(loan_data["principal_minor"], 100000)
        self.assertEqual(loan_data["outstanding_principal_minor"], 100000)
        self.assertEqual(loan_data["status"], "active")
        self.assertIsNotNone(loan_data["start_date"])
        self.assertIsNotNone(loan_data["due_date"])

        disbursement = PlatformLoanDisbursement.query.filter_by(
            id=UUID(disbursement_data["id"])
        ).one()
        entry = db.session.get(
            PlatformJournalEntry,
            UUID(disbursement_data["journal_entry_id"]),
        )

        self.assertEqual(disbursement.amount_minor, 100000)
        self.assertEqual(entry.status, "posted")

        lines = PlatformJournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            sum(line.debit_minor for line in lines),
            100000,
        )
        self.assertEqual(
            sum(line.credit_minor for line in lines),
            100000,
        )
        self.assertEqual(
            PlatformLoanInstallment.query.filter_by(
                loan_id=loan.id
            ).count(),
            6,
        )

    def test_platform_owner_can_record_partial_platform_loan_disbursement(self):
        financial_account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        investor, _ = self.create_platform_investor_with_capital(
            financial_account,
            amount_minor=100000,
        )

        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "financial_account_id": str(financial_account.id),
                "customer_financial_account_id": str(self.source_account.id),
                "amount_minor": 40000,
                "funding_source": "external_investor",
                "investor_allocations": [
                    {
                        "investor_id": str(investor.id),
                        "amount_minor": 40000,
                    }
                ],
                "disbursement_date": "2026-11-16T10:00:00+03:00",
            },
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(
            response.json["disbursement"]["amount_minor"],
            40000,
        )
        self.assertEqual(
            response.json["disbursement"]["funding_source"],
            "external_investor",
        )
        self.assertEqual(response.json["loan"]["principal_minor"], 40000)
        self.assertEqual(
            response.json["loan"]["outstanding_principal_minor"],
            40000,
        )
        self.assertEqual(response.json["loan"]["status"], "funding")
        self.assertIsNone(response.json["loan"]["start_date"])
        self.assertIsNone(response.json["loan"]["due_date"])
        self.assertEqual(
            PlatformLoanInstallment.query.filter_by(
                loan_id=loan.id
            ).count(),
            0,
        )

    def test_customer_cannot_disburse_platform_loan(self):
        financial_account = self.create_platform_accounting_setup()
        application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=application.id
        )
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "financial_account_id": str(financial_account.id),
                "amount_minor": 100000,
                "funding_source": "pesapilot_capital",
                "disbursement_date": "2026-11-16T10:00:00+03:00",
            },
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(
            PlatformLoanDisbursement.query.filter_by(
                loan_id=loan.id
            ).count(),
            0,
        )
        self.assertEqual(loan.principal_minor, 0)
        self.assertEqual(loan.status, "created")

    def test_platform_owner_can_approve_pending_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.flush()
        db.session.add(PlatformLoanAffordabilityAssessment(
            application_id=application.id,
            version=1,
            status="verified",
            estimated_affordable_principal_minor=100000,
            recommended_monthly_instalment_minor=0,
            report={},
            reasons=[],
        ))
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "approved",
                "decision_notes": "PRIVATE: internal approval review.",
                "customer_decision_message": "Your application has been approved.",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["application"]["status"], "approved")
        self.assertEqual(
            response.json["application"]["approved_principal_minor"],
            100000,
        )
        self.assertEqual(
            response.json["application"]["decision_notes"],
            "PRIVATE: internal approval review.",
        )
        self.assertEqual(
            response.json["application"]["customer_decision_message"],
            "Your application has been approved.",
        )

        db.session.refresh(application)
        self.assertEqual(application.reviewed_by, self.user.id)
        self.assertIsNotNone(application.reviewed_at)

        repeated = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={"decision": "approved"},
        )
        self.assertEqual(repeated.status_code, 409, repeated.json)

    def test_platform_owner_can_partially_approve_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.flush()
        db.session.add(PlatformLoanAffordabilityAssessment(
            application_id=application.id,
            version=1,
            status="verified",
            estimated_affordable_principal_minor=100000,
            recommended_monthly_instalment_minor=0,
            report={},
            reasons=[],
        ))
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "approved",
                "approved_principal_minor": 75000,
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            response.json["application"]["approved_principal_minor"],
            75000,
        )
        db.session.refresh(application)
        self.assertEqual(application.approved_principal_minor, 75000)

    def test_platform_owner_cannot_approve_more_than_requested(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "approved",
                "approved_principal_minor": 120000,
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        db.session.refresh(application)
        self.assertEqual(application.status, "pending")
        self.assertIsNone(application.approved_principal_minor)
        self.assertIsNone(application.reviewed_at)

    def test_platform_owner_cannot_approve_zero_amount(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "approved",
                "approved_principal_minor": 0,
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        db.session.refresh(application)
        self.assertEqual(application.status, "pending")
        self.assertIsNone(application.approved_principal_minor)

    def test_platform_owner_can_reject_pending_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Personal expenses",
            status="pending",
        )
        db.session.add(application)
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "rejected",
                "decision_notes": "Insufficient eligibility.",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["application"]["status"], "rejected")
        self.assertEqual(
            response.json["application"]["decision_notes"],
            "Insufficient eligibility.",
        )

        db.session.refresh(application)
        self.assertEqual(application.reviewed_by, self.user.id)
        self.assertIsNotNone(application.reviewed_at)

    def test_rejected_application_cannot_have_approved_principal(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Personal expenses",
            status="pending",
        )
        db.session.add(application)
        db.session.add(PlatformAdmin(
            user_id=self.user.id,
            role="owner",
            is_active=True,
        ))
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={
                "decision": "rejected",
                "approved_principal_minor": 50000,
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        db.session.refresh(application)
        self.assertEqual(application.status, "pending")
        self.assertIsNone(application.approved_principal_minor)

    def test_customer_cannot_decide_platform_loan_application(self):
        product = self.create_platform_loan_product()
        application = PlatformLoanApplication(
            customer_id=self.user.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Business stock",
            status="pending",
        )
        db.session.add(application)
        db.session.commit()

        response = self.client.post(
            f"/api/v1/platform/owner/applications/{application.id}/decision",
            headers=self.headers,
            json={"decision": "approved"},
        )

        self.assertEqual(response.status_code, 403, response.json)
        db.session.refresh(application)
        self.assertEqual(application.status, "pending")
        self.assertIsNone(application.reviewed_at)

    def test_customer_products_are_locked_until_eligibility_check(self):
        self.create_platform_loan_product()
        self.create_platform_loan_product(is_active=False)
        response = self.client.get(
            "/api/v1/platform/products", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["count"], 0)
        self.assertEqual(response.json["products"], [])
        self.assertTrue(response.json["eligibility_required"])

    def test_customer_eligibility_requires_explicit_consent(self):
        response = self.client.post(
            "/api/v1/platform/eligibility",
            headers=self.headers,
            json={"affordability_consent": False},
        )
        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("consent", response.json["message"].lower())

    def test_customer_without_verified_income_is_not_eligible(self):
        self.create_platform_loan_product()
        response = self.client.post(
            "/api/v1/platform/eligibility",
            headers=self.headers,
            json={"affordability_consent": True},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(response.json["eligible"])
        self.assertEqual(response.json["months_with_verified_income"], 0)
        self.assertEqual(response.json["evidence_window_months"], 6)
        self.assertEqual(response.json["products"], [])

    def test_customer_with_one_month_at_minimum_verified_average_can_see_products(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        response = self.client.post(
            "/api/v1/platform/eligibility",
            headers=self.headers,
            json={"affordability_consent": True},
        )
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["eligible"])
        self.assertEqual(response.json["months_with_verified_income"], 1)
        self.assertEqual(
            response.json["evidenced_average_monthly_income_minor"], 600000
        )
        self.assertEqual(
            response.json["minimum_average_monthly_income_minor"], 100000
        )
        self.assertEqual(len(response.json["products"]), 1)
        self.assertEqual(response.json["products"][0]["id"], str(product.id))

    def test_verified_income_average_uses_months_with_income_records(self):
        self.create_platform_loan_product()
        now = datetime.now(timezone.utc)
        self.create_verified_income_evidence(600000, now)
        self.create_verified_income_evidence(
            600000, now - timedelta(days=35)
        )

        response = self.client.post(
            "/api/v1/platform/eligibility",
            headers=self.headers,
            json={"affordability_consent": True},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["months_with_verified_income"], 2)
        self.assertEqual(
            response.json["evidenced_average_monthly_income_minor"], 600000
        )

    def test_verified_income_older_than_six_months_is_excluded(self):
        self.create_platform_loan_product()
        now = datetime.now(timezone.utc)
        self.create_verified_income_evidence(
            900000, now - timedelta(days=220)
        )
        self.create_verified_income_evidence(600000, now)

        response = self.client.post(
            "/api/v1/platform/eligibility",
            headers=self.headers,
            json={"affordability_consent": True},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["months_with_verified_income"], 1)
        self.assertEqual(
            response.json["evidenced_average_monthly_income_minor"], 600000
        )

    def test_customer_platform_products_require_authentication(self):
        response = self.client.get("/api/v1/platform/products")

        self.assertEqual(response.status_code, 401, response.json)


    def test_inactive_customer_cannot_submit_platform_application(self):
        product = self.create_platform_loan_product()
        self.user.is_active = False
        db.session.commit()

        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(product),
        )

        self.assertEqual(response.status_code, 401, response.json)
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id
            ).count(),
            0,
        )

    def test_unaffordable_application_is_rejected_without_persisting_records(self):
        product = self.create_platform_loan_product()
        applications_before = PlatformLoanApplication.query.filter_by(
            customer_id=self.user.id
        ).count()
        details_before = PlatformLoanApplicationDetails.query.count()
        assessments_before = PlatformLoanAffordabilityAssessment.query.count()

        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product,
                monthly_income_minor=100000,
                monthly_expenses_minor=100000,
                existing_monthly_repayments_minor=0,
            ),
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("not yet eligible", response.json["message"].lower())
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id
            ).count(),
            applications_before,
        )
        self.assertEqual(
            PlatformLoanApplicationDetails.query.count(),
            details_before,
        )
        self.assertEqual(
            PlatformLoanAffordabilityAssessment.query.count(),
            assessments_before,
        )

    def test_application_above_personal_affordability_is_rejected_without_records(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        applications_before = PlatformLoanApplication.query.filter_by(
            customer_id=self.user.id
        ).count()
        details_before = PlatformLoanApplicationDetails.query.count()
        assessments_before = PlatformLoanAffordabilityAssessment.query.count()

        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product,
                requested_principal_minor=product.max_principal_minor,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("above your assessed affordability", response.json["message"])
        self.assertIn(product.currency_code, response.json["message"])
        self.assertIn("Reduce the requested amount", response.json["message"])
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id
            ).count(),
            applications_before,
        )
        self.assertEqual(PlatformLoanApplicationDetails.query.count(), details_before)
        self.assertEqual(
            PlatformLoanAffordabilityAssessment.query.count(),
            assessments_before,
        )

    def test_customer_can_submit_platform_loan_application(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product, monthly_income_minor=600000,
                monthly_expenses_minor=0,
            ),
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])
        self.assertNotIn("decision_notes", response.json["application"])
        self.assertIn("customer_decision_message", response.json["application"])
        self.assertEqual(
            response.json["application"]["status"],
            "pending",
        )
        self.assertEqual(
            response.json["application"]["requested_principal_minor"],
            100000,
        )

        application = db.session.get(
            PlatformLoanApplication,
            UUID(response.json["application"]["id"]),
        )
        self.assertIsNotNone(application)
        self.assertEqual(application.customer_id, self.user.id)
        self.assertEqual(application.product_id, product.id)

        assessment_data = response.json["affordability_assessment"]
        self.assertEqual(assessment_data["status"], "verified")
        self.assertEqual(
            assessment_data["recommended_monthly_instalment_minor"], 210000
        )
        self.assertGreaterEqual(
            assessment_data["estimated_affordable_principal_minor"],
            0,
        )

        details = db.session.get(PlatformLoanApplicationDetails, application.id)
        self.assertIsNotNone(details)
        self.assertNotEqual(details.national_id_encrypted, "12345678")
        self.assertEqual(
            decrypt_national_id(details.national_id_encrypted),
            "12345678",
        )
        self.assertNotIn("national_id", response.json)
        self.assertNotIn(details.national_id_encrypted, str(response.json))

        assessment = PlatformLoanAffordabilityAssessment.query.filter_by(
            application_id=application.id,
            version=1,
        ).one()
        self.assertEqual(assessment.status, "verified")
        self.assertEqual(
            assessment.report["capacity_verified_monthly_income_minor"],
            600000,
        )
        self.assertTrue(assessment.report["assessment_is_advisory"])
        self.assertFalse(
            assessment.report["automatic_approval_or_rejection"]
        )

    def test_customer_cannot_submit_second_pending_application(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        payload = self.platform_application_payload(
            product, monthly_income_minor=100000, monthly_expenses_minor=0
        )

        first = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(first.status_code, 201, first.json)

        second = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=payload,
        )

        self.assertEqual(second.status_code, 409, second.json)
        self.assertIn("pending", second.json["message"].lower())
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id,
                status="pending",
            ).count(),
            1,
        )

    def test_customer_cannot_apply_for_different_product_while_pending(self):
        first_product = self.create_platform_loan_product()
        second_product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)

        first = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                first_product,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )
        self.assertEqual(first.status_code, 201, first.json)

        second = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                second_product,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )

        self.assertEqual(second.status_code, 409, second.json)
        self.assertIn("pending", second.json["message"].lower())
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id,
                status="pending",
            ).count(),
            1,
        )

    def test_customer_cannot_apply_while_platform_loan_is_outstanding(self):
        approved_application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=approved_application.id
        )
        loan.status = "active"
        loan.principal_minor = 100000
        loan.outstanding_principal_minor = 100000

        new_product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        db.session.commit()

        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                new_product,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )

        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn("not fully settled", response.json["message"].lower())
        self.assertEqual(
            PlatformLoanApplication.query.filter_by(
                customer_id=self.user.id,
                status="pending",
            ).count(),
            0,
        )

    def test_customer_can_apply_after_platform_loan_is_fully_settled(self):
        approved_application = self.create_approved_platform_application()
        loan = create_platform_loan_from_application(
            application_id=approved_application.id
        )
        loan.status = "paid"
        loan.principal_minor = 100000
        loan.outstanding_principal_minor = 0

        db.session.add(PlatformLoanInstallment(
            loan_id=loan.id,
            installment_number=1,
            due_date=date.today(),
            amount_minor=100000,
            principal_minor=100000,
            interest_minor=0,
            paid_minor=100000,
            status="paid",
        ))

        new_product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        db.session.commit()

        response = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                new_product,
                monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

    def test_customer_can_withdraw_pending_application(self):
        product = self.create_platform_loan_product()
        self.create_verified_income_evidence(600000)
        submitted = self.client.post(
            "/api/v1/platform/applications",
            headers=self.headers,
            json=self.platform_application_payload(
                product, monthly_income_minor=100000,
                monthly_expenses_minor=0,
            ),
        )
        self.assertEqual(submitted.status_code, 201, submitted.json)

        application_id = submitted.json["application"]["id"]

        application = db.session.get(
            PlatformLoanApplication,
            UUID(application_id),
        )
        application.decision_notes = "PRIVATE: internal risk review."
        application.customer_decision_message = "Your application is being withdrawn."
        db.session.commit()

        response = self.client.delete(
            f"/api/v1/platform/applications/{application_id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertNotIn("decision_notes", response.json["application"])
        self.assertEqual(
            response.json["application"]["customer_decision_message"],
            "Your application is being withdrawn.",
        )
        self.assertEqual(
            response.json["application"]["status"],
            "withdrawn",
        )
        self.assertIsNotNone(
            response.json["application"]["withdrawn_at"]
        )

        application = PlatformLoanApplication.query.filter_by(
            id=UUID(application_id),
            customer_id=self.user.id,
        ).one()
        self.assertEqual(application.status, "withdrawn")

    def test_customer_cannot_access_another_customers_application(self):
        product = self.create_platform_loan_product()

        other_customer = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(other_customer)
        db.session.flush()

        application = PlatformLoanApplication(
            customer_id=other_customer.id,
            product_id=product.id,
            requested_principal_minor=100000,
            term_count=6,
            purpose="Private application",
            status="pending",
        )
        db.session.add(application)
        db.session.commit()

        listing = self.client.get(
            "/api/v1/platform/applications",
            headers=self.headers,
        )
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json["count"], 0)
        self.assertEqual(listing.json["applications"], [])

        withdrawal = self.client.delete(
            f"/api/v1/platform/applications/{application.id}",
            headers=self.headers,
        )
        self.assertEqual(withdrawal.status_code, 404, withdrawal.json)

        db.session.refresh(application)
        self.assertEqual(application.status, "pending")

    def test_list_expense_categories(self):
        self.create_category("Transport")
        self.create_category("Food")

        response = self.client.get(
            f"/api/v1/budgets/categories?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["count"] if "count" in response.json else len(response.json["categories"]), 2)
        self.assertEqual(
            [item["name"] for item in response.json["categories"]],
            ["Food", "Transport"],
        )

    def test_duplicate_expense_category_is_rejected(self):
        first = self.create_category("Food")
        self.assertEqual(first.status_code, 201, first.json)

        duplicate = self.create_category("  fOoD  ")

        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already exists", duplicate.json["message"].lower())
        self.assertEqual(ExpenseCategory.query.filter_by(
            workspace_id=self.workspace.id
        ).count(), 1)

    def test_viewer_cannot_create_expense_category(self):
        self.membership.role = "viewer"
        db.session.commit()

        response = self.create_category("Food")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            ExpenseCategory.query.filter_by(
                workspace_id=self.workspace.id
            ).count(),
            0,
        )

    def create_budget(self, category_id, amount=50000,
                      start="2026-11-01", end="2026-11-30"):
        return self.client.post(
            "/api/v1/budgets",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "category_id": str(category_id),
                "amount_minor": amount,
                "period_start": start,
                "period_end": end,
            },
        )

    def create_multi_category_budget(
        self,
        allocations,
        start="2026-11-01",
        end="2026-11-30",
    ):
        return self.client.post(
            "/api/v1/budgets",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "categories": allocations,
                "period_start": start,
                "period_end": end,
            },
        )

    def create_expense_entry(
        self,
        category_id,
        amount_minor,
        transaction_date,
        post=True,
    ):
        category = ExpenseCategory.query.filter_by(
            id=UUID(str(category_id)),
            workspace_id=self.workspace.id,
        ).one()

        entry = create_draft_entry(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            reference=f"TEST-EXP-{uuid4().hex[:12]}",
            description="Budget calculation test expense",
            transaction_date=transaction_date,
            lines=[
                {
                    "ledger_account_id": str(category.ledger_account_id),
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(self.source_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                },
            ],
        )

        if post:
            post_entry(
                entry_id=entry.id,
                workspace_id=self.workspace.id,
                posted_by=self.user.id,
            )

        db.session.commit()
        return entry

    def create_income_entry(self, amount_minor, transaction_date):
        income_ledger = LedgerAccount(
            workspace_id=self.workspace.id,
            financial_account_id=None,
            code=f"INC-{uuid4().hex[:12]}",
            name="Test Income",
            account_class="income",
            normal_balance="credit",
            currency_code="KES",
            is_system=False,
            is_active=True,
        )
        db.session.add(income_ledger)
        db.session.flush()

        entry = create_draft_entry(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            reference=f"TEST-INC-{uuid4().hex[:12]}",
            description="Dashboard test income",
            transaction_date=transaction_date,
            lines=[
                {
                    "ledger_account_id": str(self.source_ledger.id),
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": str(income_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                },
            ],
        )
        post_entry(
            entry_id=entry.id,
            workspace_id=self.workspace.id,
            posted_by=self.user.id,
        )
        db.session.commit()
        return entry

    def get_dashboard(self):
        return self.client.get(
            f"/api/v1/finance/dashboard?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

    def get_budget_list(self):
        return self.client.get(
            f"/api/v1/budgets?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

    def test_budget_actual_includes_posted_expenses(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        created = self.create_budget(category_id, amount=100000)
        self.assertEqual(created.status_code, 201, created.json)

        self.create_expense_entry(
            category_id,
            25000,
            datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
        )

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        budget = response.json["budgets"][0]
        self.assertEqual(budget["actual_minor"], 25000)
        self.assertEqual(budget["remaining_minor"], 75000)
        self.assertEqual(budget["usage_percentage"], 25.0)
        self.assertFalse(budget["is_over_budget"])

    def test_reversed_expense_no_longer_counts_against_budget(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        created = self.create_budget(category_id, amount=100000)
        self.assertEqual(created.status_code, 201, created.json)

        transaction_date = datetime(
            2026, 11, 15, 12, 0, tzinfo=timezone.utc
        )
        original = self.create_expense_entry(
            category_id,
            25000,
            transaction_date,
        )

        before_reversal = self.get_budget_list()
        self.assertEqual(before_reversal.status_code, 200)
        self.assertEqual(
            before_reversal.json["budgets"][0]["actual_minor"],
            25000,
        )

        reversal = reverse_entry(
            entry_id=original.id,
            workspace_id=self.workspace.id,
            reversed_by=self.user.id,
            reference=f"TEST-REV-{uuid4().hex[:12]}",
            transaction_date=transaction_date,
        )
        db.session.commit()

        self.assertEqual(original.status, "reversed")
        self.assertEqual(reversal.status, "posted")
        self.assertEqual(reversal.reversal_of_id, original.id)

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        budget = response.json["budgets"][0]
        self.assertEqual(budget["actual_minor"], 0)
        self.assertEqual(budget["remaining_minor"], 100000)
        self.assertEqual(budget["usage_percentage"], 0)
        self.assertFalse(budget["is_over_budget"])

    def test_budget_reports_overspending(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        # This test checks budget overspending, not insufficient account funds.
        extra = create_draft_entry(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            reference=f"TEST-BUDGET-FUND-{uuid4().hex[:12]}",
            description="Additional test funding",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": self.source_ledger.id,
                    "debit_minor": 50000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": self.equity_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": 50000,
                },
            ],
        )
        post_entry(
            entry_id=extra.id,
            workspace_id=self.workspace.id,
            posted_by=self.user.id,
        )
        db.session.commit()

        created = self.create_budget(category_id, amount=100000)
        self.assertEqual(created.status_code, 201, created.json)

        self.create_expense_entry(
            category_id,
            125000,
            datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
        )

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        budget = response.json["budgets"][0]
        self.assertEqual(budget["actual_minor"], 125000)
        self.assertEqual(budget["remaining_minor"], -25000)
        self.assertEqual(budget["usage_percentage"], 125.0)
        self.assertTrue(budget["is_over_budget"])

    def test_budget_ignores_draft_expenses(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        created = self.create_budget(category_id, amount=100000)
        self.assertEqual(created.status_code, 201, created.json)

        entry = self.create_expense_entry(
            category_id,
            25000,
            datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc),
            post=False,
        )
        self.assertEqual(entry.status, "draft")

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        budget = response.json["budgets"][0]
        self.assertEqual(budget["actual_minor"], 0)
        self.assertEqual(budget["remaining_minor"], 100000)

    def test_budget_ignores_expenses_outside_period(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        created = self.create_budget(category_id, amount=100000)
        self.assertEqual(created.status_code, 201, created.json)

        self.create_expense_entry(
            category_id,
            25000,
            datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        )

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        budget = response.json["budgets"][0]
        self.assertEqual(budget["actual_minor"], 0)
        self.assertEqual(budget["remaining_minor"], 100000)

    def test_create_budget(self):
        category_response = self.create_category("Food")
        self.assertEqual(category_response.status_code, 201)

        category_id = category_response.json["category"]["id"]
        response = self.create_budget(category_id)

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        budget = response.json["budget"]
        self.assertEqual(budget["amount_minor"], 50000)
        self.assertEqual(budget["actual_minor"], 0)
        self.assertEqual(budget["remaining_minor"], 50000)
        self.assertEqual(budget["usage_percentage"], 0)
        self.assertEqual(budget["currency_code"], "KES")
        self.assertEqual(budget["period_start"], "2026-11-01")
        self.assertEqual(budget["period_end"], "2026-11-30")
        self.assertEqual(budget["category"]["name"], "Food")
        self.assertEqual(len(budget["categories"]), 1)
        self.assertEqual(
            budget["categories"][0]["category"]["name"],
            "Food",
        )
        self.assertEqual(
            budget["categories"][0]["amount_minor"],
            50000,
        )

    def test_list_budgets(self):
        first = self.create_category("Food")
        second = self.create_category("Transport")

        created = self.create_multi_category_budget([
            {
                "category_id": first.json["category"]["id"],
                "amount_minor": 50000,
            },
            {
                "category_id": second.json["category"]["id"],
                "amount_minor": 80000,
            },
        ])

        self.assertEqual(created.status_code, 201, created.json)

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json["budgets"]), 1)

        budget = response.json["budgets"][0]
        self.assertEqual(budget["amount_minor"], 130000)
        self.assertIsNone(budget["category"])

        allocations = {
            item["category"]["name"]: item["amount_minor"]
            for item in budget["categories"]
        }
        self.assertEqual(
            allocations,
            {"Food": 50000, "Transport": 80000},
        )

    def test_multi_category_budget_tracks_category_and_parent_spending(self):
        food = self.create_category("Food")
        transport = self.create_category("Transport")

        food_id = food.json["category"]["id"]
        transport_id = transport.json["category"]["id"]

        created = self.create_multi_category_budget([
            {"category_id": food_id, "amount_minor": 100000},
            {"category_id": transport_id, "amount_minor": 50000},
        ])

        self.assertEqual(created.status_code, 201, created.json)

        transaction_date = datetime(
            2026, 11, 15, 12, 0, tzinfo=timezone.utc
        )
        self.create_expense_entry(food_id, 25000, transaction_date)
        self.create_expense_entry(transport_id, 10000, transaction_date)

        response = self.get_budget_list()

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json["budgets"]), 1)

        budget = response.json["budgets"][0]
        self.assertEqual(budget["amount_minor"], 150000)
        self.assertEqual(budget["actual_minor"], 35000)
        self.assertEqual(budget["remaining_minor"], 115000)
        self.assertEqual(budget["usage_percentage"], 23.33)
        self.assertFalse(budget["is_over_budget"])

        categories = {
            item["category"]["name"]: item
            for item in budget["categories"]
        }

        self.assertEqual(categories["Food"]["amount_minor"], 100000)
        self.assertEqual(categories["Food"]["actual_minor"], 25000)
        self.assertEqual(categories["Food"]["remaining_minor"], 75000)

        self.assertEqual(categories["Transport"]["amount_minor"], 50000)
        self.assertEqual(categories["Transport"]["actual_minor"], 10000)
        self.assertEqual(categories["Transport"]["remaining_minor"], 40000)

    def test_multi_category_budget_rejects_duplicate_categories(self):
        food = self.create_category("Food")
        food_id = food.json["category"]["id"]

        response = self.create_multi_category_budget([
            {"category_id": food_id, "amount_minor": 50000},
            {"category_id": food_id, "amount_minor": 30000},
        ])

        self.assertEqual(response.status_code, 400, response.json)
        self.assertIn("only appear once", response.json["message"])

        self.assertEqual(
            Budget.query.filter_by(
                workspace_id=self.workspace.id,
            ).count(),
            0,
        )

    def test_duplicate_budget_period_is_rejected(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        first = self.create_budget(category_id)
        self.assertEqual(first.status_code, 201, first.json)

        duplicate = self.create_budget(category_id, amount=70000)

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(
            Budget.query.filter_by(
                workspace_id=self.workspace.id,
            ).count(),
            1,
        )

    def test_budget_rejects_invalid_amounts(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        for amount in (0, -1, True, 1.5, "50000"):
            with self.subTest(amount=amount):
                response = self.create_budget(
                    category_id,
                    amount=amount,
                )
                self.assertEqual(response.status_code, 400)

        self.assertEqual(
            Budget.query.filter_by(
                workspace_id=self.workspace.id,
            ).count(),
            0,
        )

    def test_budget_rejects_invalid_dates(self):
        category = self.create_category("Food")
        category_id = category.json["category"]["id"]

        invalid_periods = [
            ("not-a-date", "2026-11-30"),
            ("2026-11-01", "not-a-date"),
            ("2026-12-01", "2026-11-01"),
        ]

        for start, end in invalid_periods:
            with self.subTest(start=start, end=end):
                response = self.create_budget(
                    category_id,
                    start=start,
                    end=end,
                )
                self.assertEqual(response.status_code, 400)

        self.assertEqual(
            Budget.query.filter_by(
                workspace_id=self.workspace.id,
            ).count(),
            0,
        )

    def test_viewer_cannot_create_budget(self):
        category = self.create_category("Food")
        self.membership.role = "viewer"
        db.session.commit()

        response = self.create_budget(
            category.json["category"]["id"],
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            Budget.query.filter_by(
                workspace_id=self.workspace.id,
            ).count(),
            0,
        )

    def test_health_endpoint(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])

    def test_database_health_endpoint(self):
        response = self.client.get("/api/v1/health/db")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])

    def test_create_personal_loan(self):
        response = self.create_personal_loan()

        self.assertEqual(response.status_code, 201, response.json)
        self.assertTrue(response.json["success"])

        loan = response.json["loan"]
        self.assertEqual(loan["direction"], "borrowed")
        self.assertEqual(loan["counterparty_name"], "Jane Doe")
        self.assertEqual(loan["principal_minor"], 100000)
        self.assertEqual(loan["currency_code"], "KES")
        self.assertEqual(loan["status"], "draft")

        saved = db.session.get(PersonalLoan, UUID(loan["id"]))
        self.assertIsNotNone(saved)
        self.assertEqual(saved.created_by, self.user.id)

    def test_list_personal_loans(self):
        self.create_personal_loan()
        self.create_personal_loan(
            direction="lent",
            counterparty_name="John Doe",
        )

        response = self.client.get(
            f"/api/v1/loans?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["count"], 2)
        self.assertEqual(len(response.json["loans"]), 2)

    def test_viewer_cannot_create_personal_loan(self):
        self.membership.role = "viewer"
        db.session.commit()

        response = self.create_personal_loan()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            PersonalLoan.query.filter_by(
                workspace_id=self.workspace.id
            ).count(),
            0,
        )

    def test_non_member_cannot_access_personal_loans(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        token = create_access_token(identity=str(outsider.id))
        headers = {"Authorization": f"Bearer {token}"}

        response = self.client.get(
            f"/api/v1/loans?workspace_id={self.workspace.id}",
            headers=headers,
        )

        self.assertEqual(response.status_code, 404)

    def test_personal_loan_rejects_zero_principal(self):
        response = self.create_personal_loan(principal_minor=0)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            PersonalLoan.query.filter_by(
                workspace_id=self.workspace.id
            ).count(),
            0,
        )

    def test_personal_loan_rejects_interest_free_with_interest(self):
        response = self.create_personal_loan(
            loan_type="interest_free",
            interest_method="flat",
            interest_rate_percent="10",
        )

        self.assertEqual(response.status_code, 400)

    def test_personal_loan_rejects_currency_mismatch(self):
        response = self.create_personal_loan(currency_code="USD")

        self.assertEqual(response.status_code, 400)


    def test_update_personal_loan_draft(self):
        created = self.create_personal_loan()
        self.assertEqual(created.status_code, 201, created.json)

        loan_id = created.json["loan"]["id"]

        payload = {
            "workspace_id": str(self.workspace.id),
            "direction": "borrowed",
            "counterparty_name": "Updated Jane",
            "counterparty_phone": "0700111222",
            "loan_type": "interest_free",
            "principal_minor": 250000,
            "currency_code": "KES",
            "interest_method": "none",
            "interest_rate_percent": "0",
            "repayment_frequency": "monthly",
            "term_count": 1,
            "start_date": "2026-01-01",
            "due_date": "2026-02-01",
            "description": "Updated loan details",
        }

        response = self.client.patch(
            f"/api/v1/loans/{loan_id}",
            headers=self.headers,
            json=payload,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        loan = response.json["loan"]
        self.assertEqual(loan["counterparty_name"], "Updated Jane")
        self.assertEqual(loan["counterparty_phone"], "0700111222")
        self.assertEqual(loan["loan_type"], "interest_free")
        self.assertEqual(loan["principal_minor"], 250000)
        self.assertEqual(loan["interest_method"], "none")
        self.assertEqual(loan["repayment_frequency"], "monthly")
        self.assertEqual(loan["term_count"], 1)
        self.assertEqual(loan["description"], "Updated loan details")
        self.assertEqual(loan["status"], "draft")

        saved = db.session.get(PersonalLoan, UUID(loan_id))
        self.assertEqual(saved.counterparty_name, "Updated Jane")
        self.assertEqual(saved.principal_minor, 250000)

    def test_activated_personal_loan_cannot_be_updated(self):
        created = self.create_personal_loan(
            start_date="2026-01-01",
            due_date="2027-01-01",
            interest_method="none",
            interest_rate_percent="0",
        )
        self.assertEqual(created.status_code, 201, created.json)

        loan_id = created.json["loan"]["id"]

        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 200, activated.json)

        response = self.client.patch(
            f"/api/v1/loans/{loan_id}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "counterparty_name": "Changed Name",
            },
        )

        self.assertEqual(response.status_code, 409, response.json)
        saved = db.session.get(PersonalLoan, UUID(loan_id))
        self.assertEqual(saved.status, "active")
        self.assertEqual(saved.counterparty_name, "Jane Doe")

    def test_viewer_cannot_update_personal_loan(self):
        created = self.create_personal_loan()
        self.assertEqual(created.status_code, 201, created.json)

        loan_id = created.json["loan"]["id"]

        self.membership.role = "viewer"
        db.session.commit()

        response = self.client.patch(
            f"/api/v1/loans/{loan_id}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "counterparty_name": "Changed Name",
            },
        )

        self.assertEqual(response.status_code, 403, response.json)

        saved = db.session.get(PersonalLoan, UUID(loan_id))
        self.assertEqual(saved.counterparty_name, "Jane Doe")
        self.assertEqual(saved.status, "draft")

    def test_activate_personal_loan_generates_schedule(self):
        response = self.create_personal_loan(
            start_date="2026-01-01",
            due_date="2027-01-01",
            interest_method="none",
            interest_rate_percent="0",
        )
        self.assertEqual(response.status_code, 201, response.json)
        loan_id = response.json["loan"]["id"]
        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 200, activated.json)
        self.assertEqual(activated.json["loan"]["status"], "active")
        self.assertEqual(activated.json["installment_count"], 12)
        installments = LoanInstallment.query.filter_by(
            personal_loan_id=UUID(loan_id),
            workspace_id=self.workspace.id,
        ).order_by(LoanInstallment.installment_number).all()
        self.assertEqual(len(installments), 12)
        self.assertEqual(installments[0].due_date, date(2026, 2, 1))
        self.assertEqual(installments[-1].due_date, date(2027, 1, 1))
        self.assertEqual(sum(x.principal_minor for x in installments), 100000)

    def test_personal_loan_cannot_be_activated_twice(self):
        response = self.create_personal_loan(
            start_date="2026-01-01", due_date="2027-01-01",
            interest_method="none", interest_rate_percent="0",
        )
        loan_id = response.json["loan"]["id"]
        url = f"/api/v1/loans/{loan_id}/activate"
        payload = {"workspace_id": str(self.workspace.id)}
        first = self.client.post(url, headers=self.headers, json=payload)
        second = self.client.post(url, headers=self.headers, json=payload)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(LoanInstallment.query.filter_by(
            personal_loan_id=UUID(loan_id)
        ).count(), 12)

    def test_viewer_cannot_activate_personal_loan(self):
        response = self.create_personal_loan(
            start_date="2026-01-01", due_date="2027-01-01",
            interest_method="none", interest_rate_percent="0",
        )
        loan_id = response.json["loan"]["id"]
        self.membership.role = "viewer"
        db.session.commit()
        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 403)
        self.assertEqual(db.session.get(PersonalLoan, UUID(loan_id)).status, "draft")

    def test_member_cannot_activate_personal_loan(self):
        response = self.create_personal_loan(
            start_date="2026-01-01", due_date="2027-01-01",
            interest_method="none", interest_rate_percent="0",
        )
        loan_id = response.json["loan"]["id"]
        self.membership.role = "member"
        db.session.commit()
        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 403)

    def test_nonmember_cannot_activate_personal_loan(self):
        response = self.create_personal_loan(
            start_date="2026-01-01", due_date="2027-01-01",
            interest_method="none", interest_rate_percent="0",
        )
        loan_id = response.json["loan"]["id"]
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()
        token = create_access_token(identity=str(outsider.id))
        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers={"Authorization": f"Bearer {token}"},
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 404)

    def test_invalid_schedule_does_not_activate_loan(self):
        response = self.create_personal_loan(
            start_date="2026-01-01", due_date="2026-02-01",
            term_count=12, interest_method="none",
            interest_rate_percent="0",
        )
        loan_id = response.json["loan"]["id"]
        activated = self.client.post(
            f"/api/v1/loans/{loan_id}/activate",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(activated.status_code, 400)
        self.assertEqual(db.session.get(PersonalLoan, UUID(loan_id)).status, "draft")
        self.assertEqual(LoanInstallment.query.filter_by(
            personal_loan_id=UUID(loan_id)
        ).count(), 0)


    def disburse_test_loan(self, direction="borrowed"):
        loan, _, _ = self.create_repayment_case(direction)
        response = self.client.post(
            f"/api/v1/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "financial_account_id": str(self.source_account.id),
                "disbursement_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )
        return loan, response

    def test_borrowed_loan_disbursement_posts_correct_accounting(self):
        starting_balance = self.source_balance_minor()
        loan, response = self.disburse_test_loan("borrowed")

        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["disbursement"]["amount_minor"], 9000)
        self.assertEqual(response.json["disbursement"]["status"], "posted")
        self.assertEqual(self.source_balance_minor(), starting_balance + 9000)

        record = LoanDisbursement.query.filter_by(
            personal_loan_id=loan.id
        ).one()
        lines = JournalLine.query.filter_by(
            journal_entry_id=record.journal_entry_id
        ).all()

        self.assertEqual(len(lines), 2)
        cash_line = next(x for x in lines if x.ledger_account_id == self.source_ledger.id)
        payable = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="2000"
        ).one()
        payable_line = next(x for x in lines if x.ledger_account_id == payable.id)

        self.assertEqual(cash_line.functional_debit_minor, 9000)
        self.assertEqual(payable_line.functional_credit_minor, 9000)

    def test_lent_loan_disbursement_posts_correct_accounting(self):
        starting_balance = self.source_balance_minor()
        loan, response = self.disburse_test_loan("lent")

        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.source_balance_minor(), starting_balance - 9000)

        record = LoanDisbursement.query.filter_by(
            personal_loan_id=loan.id
        ).one()
        lines = JournalLine.query.filter_by(
            journal_entry_id=record.journal_entry_id
        ).all()

        self.assertEqual(len(lines), 2)
        cash_line = next(x for x in lines if x.ledger_account_id == self.source_ledger.id)
        receivable = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="1200"
        ).one()
        receivable_line = next(x for x in lines if x.ledger_account_id == receivable.id)

        self.assertEqual(cash_line.functional_credit_minor, 9000)
        self.assertEqual(receivable_line.functional_debit_minor, 9000)

    def test_lent_loan_disbursement_rejects_insufficient_funds(self):
        loan, _, _ = self.create_repayment_case("lent")

        # Reduce the opening KES 1,000.00 balance to KES 40.00.
        adjustment = create_draft_entry(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            reference=f"TEST-REDUCE-{uuid4().hex}",
            description="Reduce test wallet balance",
            transaction_date=datetime.now(timezone.utc),
            lines=[
                {
                    "ledger_account_id": self.equity_ledger.id,
                    "debit_minor": 96000,
                    "credit_minor": 0,
                },
                {
                    "ledger_account_id": self.source_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": 96000,
                },
            ],
        )
        post_entry(
            entry_id=adjustment.id,
            workspace_id=self.workspace.id,
            posted_by=self.user.id,
        )
        db.session.commit()

        starting_balance = self.source_balance_minor()
        self.assertEqual(starting_balance, 4000)

        response = self.client.post(
            f"/api/v1/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "financial_account_id": str(self.source_account.id),
                "disbursement_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )

        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json["available_minor"], 4000)
        self.assertEqual(response.json["required_minor"], 9000)
        self.assertEqual(self.source_balance_minor(), starting_balance)
        self.assertEqual(
            LoanDisbursement.query.filter_by(personal_loan_id=loan.id).count(),
            0,
        )

    def test_loan_cannot_be_disbursed_twice(self):
        loan, first = self.disburse_test_loan("borrowed")
        second = self.client.post(
            f"/api/v1/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "financial_account_id": str(self.source_account.id),
                "disbursement_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )

        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(second.status_code, 409, second.json)
        self.assertEqual(
            LoanDisbursement.query.filter_by(personal_loan_id=loan.id).count(),
            1,
        )

    def test_viewer_cannot_disburse_loan(self):
        loan, _, _ = self.create_repayment_case("borrowed", role="viewer")
        response = self.client.post(
            f"/api/v1/loans/{loan.id}/disburse",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "financial_account_id": str(self.source_account.id),
                "disbursement_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )

        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(
            LoanDisbursement.query.filter_by(personal_loan_id=loan.id).count(),
            0,
        )

    def make_schedule_loan(self, **overrides):
        start = date(2026, 1, 1)
        values = {
            "principal_minor": 120000,
            "interest_method": "none",
            "interest_rate_percent": Decimal("0"),
            "repayment_frequency": "monthly",
            "term_count": 12,
            "start_date": start,
            "due_date": date(2027, 1, 1),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_interest_free_schedule_repaid_in_equal_installments(self):
        loan = self.make_schedule_loan()
        schedule = build_repayment_schedule(loan)

        self.assertEqual(len(schedule), 12)
        self.assertEqual(
            sum(item["principal_minor"] for item in schedule), 120000
        )
        self.assertEqual(
            sum(item["interest_minor"] for item in schedule), 0
        )
        self.assertEqual(schedule[-1]["due_date"], loan.due_date)
        self.assertEqual(
            [item["amount_minor"] for item in schedule[:-1]],
            [10000] * 11,
        )
        self.assertEqual(schedule[-1]["amount_minor"], 10000)

    def test_flat_interest_schedule_uses_original_principal(self):
        loan = self.make_schedule_loan(
            interest_method="flat",
            interest_rate_percent=Decimal("12"),
        )
        schedule = build_repayment_schedule(loan)

        total_interest = sum(
            item["interest_minor"] for item in schedule
        )
        total_principal = sum(
            item["principal_minor"] for item in schedule
        )

        self.assertEqual(total_principal, 120000)
        self.assertGreater(total_interest, 0)
        self.assertAlmostEqual(total_interest, 175200, delta=12)
        self.assertGreater(
            max(item["interest_minor"] for item in schedule),
            min(item["interest_minor"] for item in schedule),
        )

    def test_monthly_rate_is_prorated_over_thirty_days(self):
        for method in ("flat", "reducing"):
            with self.subTest(interest_method=method):
                loan = self.make_schedule_loan(
                    principal_minor=200000,
                    interest_method=method,
                    interest_rate_percent=Decimal("8"),
                    repayment_frequency="one_time",
                    term_count=1,
                    start_date=date(2026, 1, 1),
                    due_date=date(2026, 1, 31),
                )
                schedule = build_repayment_schedule(loan)

                self.assertEqual(schedule[0]["interest_minor"], 16000)
                self.assertEqual(schedule[0]["amount_minor"], 216000)

    def test_weekly_interest_uses_actual_days_over_thirty(self):
        loan = self.make_schedule_loan(
            principal_minor=200000,
            interest_method="flat",
            interest_rate_percent=Decimal("8"),
            repayment_frequency="weekly",
            term_count=1,
            start_date=date(2026, 1, 1),
            due_date=date(2026, 1, 8),
        )
        schedule = build_repayment_schedule(loan)

        self.assertEqual(schedule[0]["interest_minor"], 3733)

    def test_reducing_schedule_has_declining_interest(self):
        loan = self.make_schedule_loan(
            interest_method="reducing",
            interest_rate_percent=Decimal("12"),
        )
        schedule = build_repayment_schedule(loan)

        interests = [item["interest_minor"] for item in schedule]

        self.assertEqual(
            sum(item["principal_minor"] for item in schedule), 120000
        )
        self.assertGreater(interests[0], interests[-1])
        self.assertGreater(interests[0], interests[-1])

    def test_schedule_preserves_month_end_dates(self):
        loan = self.make_schedule_loan(
            start_date=date(2026, 1, 31),
            due_date=date(2026, 4, 30),
            term_count=3,
        )
        schedule = build_repayment_schedule(loan)

        self.assertEqual(
            [item["due_date"] for item in schedule],
            [
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 4, 30),
            ],
        )

    def test_schedule_rejects_mismatched_final_due_date(self):
        loan = self.make_schedule_loan(
            due_date=date(2026, 12, 15),
        )

        with self.assertRaises(ValueError):
            build_repayment_schedule(loan)

    def test_one_time_schedule_has_single_final_installment(self):
        loan = self.make_schedule_loan(
            interest_method="flat",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="one_time",
            term_count=1,
            due_date=date(2026, 7, 1),
        )
        schedule = build_repayment_schedule(loan)

        self.assertEqual(len(schedule), 1)
        self.assertEqual(schedule[0]["principal_minor"], 120000)
        self.assertGreater(schedule[0]["interest_minor"], 0)
        self.assertEqual(schedule[0]["due_date"], date(2026, 7, 1))

    def test_schedule_rejects_negative_amortization(self):
        loan = self.make_schedule_loan(
            interest_method="flat",
            interest_rate_percent=Decimal("1000000"),
            repayment_frequency="monthly",
            term_count=2,
            start_date=date(2026, 7, 31),
            due_date=date(2026, 9, 30),
        )

        with self.assertRaises(ValueError):
            build_repayment_schedule(loan)

    def test_list_savings_goals_empty(self):
        response = self.client.get(
            f"/api/v1/savings?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["count"], 0)

    def test_create_savings_goal(self):
        goal = self.create_goal()

        self.assertEqual(goal["name"], "Emergency Fund")
        self.assertEqual(goal["target_amount_minor"], 50000)
        self.assertEqual(goal["saved_amount_minor"], 0)
        self.assertEqual(goal["remaining_amount_minor"], 50000)
        self.assertFalse(goal["is_completed"])

        account = db.session.get(
            FinancialAccount,
            UUID(goal["financial_account_id"]),
        )
        self.assertEqual(account.account_type, "savings")

    def test_multiple_goals_can_share_one_savings_account(self):
        first_goal = self.create_goal("Emergency Fund", 50000)
        account_id = first_goal["financial_account_id"]

        second_response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": "School Fees",
                "target_amount_minor": 80000,
                "financial_account_id": account_id,
            },
        )

        self.assertEqual(second_response.status_code, 201, second_response.json)
        second_goal = second_response.json["goal"]

        self.assertEqual(second_goal["financial_account_id"], account_id)
        self.assertEqual(first_goal["financial_account_id"], account_id)
        self.assertEqual(first_goal["financial_account_name"],
                         second_goal["financial_account_name"])

        first_contribution = self.transfer(
            first_goal["id"], "contributions", 10000
        )
        second_contribution = self.transfer(
            second_goal["id"], "contributions", 20000
        )

        self.assertEqual(first_contribution.status_code, 201,
                         first_contribution.json)
        self.assertEqual(second_contribution.status_code, 201,
                         second_contribution.json)

        self.assertEqual(
            first_contribution.json["goal"]["saved_amount_minor"], 10000
        )
        self.assertEqual(
            second_contribution.json["goal"]["saved_amount_minor"], 20000
        )

        savings_ledger = LedgerAccount.query.filter_by(
            financial_account_id=UUID(account_id)
        ).first()
        self.assertIsNotNone(savings_ledger)

        posted_lines = (
            JournalLine.query
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .filter(
                JournalEntry.workspace_id == self.workspace.id,
                JournalEntry.status == "posted",
                JournalLine.ledger_account_id == savings_ledger.id,
            )
            .all()
        )
        shared_account_balance = sum(
            line.functional_debit_minor - line.functional_credit_minor
            for line in posted_lines
        )
        self.assertEqual(shared_account_balance, 30000)

    def test_goal_cannot_withdraw_another_goals_allocation(self):
        first_goal = self.create_goal("Emergency Fund", 50000)
        account_id = first_goal["financial_account_id"]

        second_response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": "School Fees",
                "target_amount_minor": 80000,
                "financial_account_id": account_id,
            },
        )
        self.assertEqual(second_response.status_code, 201,
                         second_response.json)
        second_goal = second_response.json["goal"]

        first_contribution = self.transfer(
            first_goal["id"], "contributions", 10000
        )
        second_contribution = self.transfer(
            second_goal["id"], "contributions", 20000
        )
        self.assertEqual(first_contribution.status_code, 201,
                         first_contribution.json)
        self.assertEqual(second_contribution.status_code, 201,
                         second_contribution.json)

        # The shared account holds 30,000, but this goal owns only 10,000.
        withdrawal = self.transfer(
            first_goal["id"], "withdrawals", 15000
        )
        self.assertEqual(withdrawal.status_code, 400, withdrawal.json)

        goals_response = self.client.get(
            f"/api/v1/savings?workspace_id={self.workspace.id}",
            headers=self.headers,
        )
        self.assertEqual(goals_response.status_code, 200)

        goals_by_id = {
            goal["id"]: goal
            for goal in goals_response.json["goals"]
        }
        self.assertEqual(
            goals_by_id[first_goal["id"]]["saved_amount_minor"], 10000
        )
        self.assertEqual(
            goals_by_id[second_goal["id"]]["saved_amount_minor"], 20000
        )

    def test_contribution_posts_balanced_journal(self):
        goal = self.create_goal()
        self.assertEqual(self.source_balance_minor(), 100000)

        response = self.transfer(
            goal["id"], "contributions", 25000
        )

        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.source_balance_minor(), 75000)
        self.assertTrue(response.json["success"])
        self.assertEqual(
            response.json["goal"]["saved_amount_minor"], 25000
        )
        self.assertEqual(
            response.json["goal"]["remaining_amount_minor"], 25000
        )

        entry = db.session.get(
            JournalEntry,
            UUID(response.json["journal_entry_id"]),
        )
        self.assertEqual(entry.status, "posted")

        lines = JournalLine.query.filter_by(
            journal_entry_id=entry.id
        ).all()

        self.assertEqual(len(lines), 2)
        self.assertEqual(
            sum(line.functional_debit_minor for line in lines),
            25000,
        )
        self.assertEqual(
            sum(line.functional_credit_minor for line in lines),
            25000,
        )

    def test_withdrawal_reduces_savings_balance(self):
        goal = self.create_goal()

        contribution = self.transfer(
            goal["id"], "contributions", 30000
        )
        self.assertEqual(contribution.status_code, 201)
        self.assertEqual(self.source_balance_minor(), 70000)

        withdrawal = self.transfer(
            goal["id"], "withdrawals", 10000
        )

        self.assertEqual(withdrawal.status_code, 201, withdrawal.json)
        self.assertEqual(self.source_balance_minor(), 80000)
        self.assertEqual(
            withdrawal.json["goal"]["saved_amount_minor"], 20000
        )

        entry = db.session.get(
            JournalEntry,
            UUID(withdrawal.json["journal_entry_id"]),
        )
        self.assertEqual(entry.status, "posted")

    def test_contribution_rejects_insufficient_funds(self):
        goal = self.create_goal()

        response = self.transfer(
            goal["id"], "contributions", 100001
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("insufficient funds", response.json["message"].lower())

        self.assertEqual(
            self.client.get(
                f"/api/v1/savings?workspace_id={self.workspace.id}",
                headers=self.headers,
            ).json["goals"][0]["saved_amount_minor"],
            0,
        )

    def test_withdrawal_rejects_insufficient_savings(self):
        goal = self.create_goal()

        response = self.transfer(
            goal["id"], "withdrawals", 1
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("insufficient funds", response.json["message"].lower())

    def test_transfer_rejects_boolean_amount(self):
        goal = self.create_goal()

        response = self.transfer(
            goal["id"], "contributions", True
        )

        self.assertEqual(response.status_code, 400)

    def test_create_goal_rejects_zero_target(self):
        response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": "Invalid Goal",
                "target_amount_minor": 0,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_create_goal_rejects_different_currency(self):
        response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": "Dollar Savings",
                "target_amount_minor": 50000,
                "currency": "USD",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "functional currency",
            response.json["message"].lower(),
        )

    def test_paused_goal_rejects_contribution(self):
        goal_data = self.create_goal()

        goal = db.session.get(
            SavingsGoal,
            UUID(goal_data["id"]),
        )
        goal.status = "paused"
        db.session.commit()

        entries_before = JournalEntry.query.filter_by(
            workspace_id=self.workspace.id
        ).count()

        response = self.transfer(
            goal_data["id"],
            "contributions",
            10000,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("active", response.json["message"].lower())

        entries_after = JournalEntry.query.filter_by(
            workspace_id=self.workspace.id
        ).count()

        self.assertEqual(entries_after, entries_before)

    def test_viewer_cannot_create_goal(self):
        self.membership.role = "viewer"
        db.session.commit()

        response = self.client.post(
            "/api/v1/savings",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "name": "Restricted Goal",
                "target_amount_minor": 10000,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_list_workspace_goals(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        outsider_token = create_access_token(
            identity=str(outsider.id)
        )

        response = self.client.get(
            f"/api/v1/savings?workspace_id={self.workspace.id}",
            headers={
                "Authorization": f"Bearer {outsider_token}",
            },
        )

        self.assertEqual(response.status_code, 404)

    def test_ai_manager_overview_returns_read_only_data(self):
        response = self.client.get(
            f"/api/v1/ai-manager/overview?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        data = response.json["data"]
        self.assertIn("snapshot", data)
        self.assertIn("insights", data)
        self.assertEqual(data["generated_by"], "rules")
        self.assertTrue(data["read_only"])
        self.assertIsInstance(data["insights"], list)

    def test_ai_manager_overview_rejects_non_member(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        outsider_token = create_access_token(
            identity=str(outsider.id)
        )
        response = self.client.get(
            f"/api/v1/ai-manager/overview?workspace_id={self.workspace.id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["success"])

    def test_ai_manager_overview_requires_authentication(self):
        response = self.client.get(
            f"/api/v1/ai-manager/overview?workspace_id={self.workspace.id}"
        )

        self.assertEqual(response.status_code, 401)


    def test_ai_manager_ask_requires_explicit_external_consent(self):
        with patch(
            "app.modules.ai_manager.routes.get_ai_provider"
        ) as provider_factory:
            response = self.client.post(
                "/api/v1/ai-manager/ask",
                headers=self.headers,
                json={
                    "workspace_id": str(self.workspace.id),
                    "question": "How much money do I have?",
                    "external_ai_consent": False,
                },
            )

        self.assertEqual(response.status_code, 400)
        provider_factory.assert_not_called()

    def test_ai_manager_ask_rejects_missing_consent(self):
        with patch(
            "app.modules.ai_manager.routes.get_ai_provider"
        ) as provider_factory:
            response = self.client.post(
                "/api/v1/ai-manager/ask",
                headers=self.headers,
                json={
                    "workspace_id": str(self.workspace.id),
                    "question": "How much money do I have?",
                },
            )

        self.assertEqual(response.status_code, 400)
        provider_factory.assert_not_called()

    def test_ai_manager_ask_rejects_when_external_ai_is_disabled(self):
        self.app.config["AI_EXTERNAL_ENABLED"] = False

        with patch(
            "app.modules.ai_manager.routes.get_ai_provider",
            side_effect=__import__(
                "app.services.ai_manager.providers",
                fromlist=["AIProviderUnavailable"],
            ).AIProviderUnavailable("External AI is disabled."),
        ) as provider_factory:
            response = self.client.post(
                "/api/v1/ai-manager/ask",
                headers=self.headers,
                json={
                    "workspace_id": str(self.workspace.id),
                    "question": "How much money do I have?",
                    "external_ai_consent": True,
                },
            )

        self.assertEqual(response.status_code, 503)
        provider_factory.assert_called_once()
        self.app.config["AI_EXTERNAL_ENABLED"] = False

    def test_ai_manager_ask_returns_mocked_provider_answer(self):
        provider = Mock()
        provider.generate_answer.return_value = "Your recorded net worth is KES 1,000."

        self.app.config["AI_EXTERNAL_ENABLED"] = True
        self.app.config["AI_PROVIDER"] = "groq"

        with patch(
            "app.modules.ai_manager.routes.get_ai_provider",
            return_value=provider,
        ):
            response = self.client.post(
                "/api/v1/ai-manager/ask",
                headers=self.headers,
                json={
                    "workspace_id": str(self.workspace.id),
                    "question": "What is my net worth?",
                    "external_ai_consent": True,
                },
            )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(
            response.json["data"]["answer"],
            "Your recorded net worth is KES 1,000.",
        )
        self.assertTrue(response.json["data"]["read_only"])
        self.assertTrue(response.json["data"]["external_ai_used"])

        provider.generate_answer.assert_called_once()
        context = provider.generate_answer.call_args.kwargs[
            "financial_context"
        ]
        self.assertNotIn("workspace_id", context)
        self.assertIn("summary", context)

        self.app.config["AI_EXTERNAL_ENABLED"] = False

    def test_ai_manager_ask_rejects_non_member(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        outsider_token = create_access_token(
            identity=str(outsider.id)
        )

        provider = Mock()
        self.app.config["AI_EXTERNAL_ENABLED"] = True

        with patch(
            "app.modules.ai_manager.routes.get_ai_provider",
            return_value=provider,
        ):
            response = self.client.post(
                "/api/v1/ai-manager/ask",
                headers={
                    "Authorization": f"Bearer {outsider_token}",
                },
                json={
                    "workspace_id": str(self.workspace.id),
                    "question": "What is my net worth?",
                    "external_ai_consent": True,
                },
            )

        self.assertEqual(response.status_code, 404)
        provider.generate_answer.assert_not_called()
        self.app.config["AI_EXTERNAL_ENABLED"] = False

    def test_ai_manager_ask_requires_authentication(self):
        response = self.client.post(
            "/api/v1/ai-manager/ask",
            json={
                "workspace_id": str(self.workspace.id),
                "question": "What is my net worth?",
                "external_ai_consent": True,
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_ai_snapshot_calculates_six_month_income_and_spending_trends(self):
        now = datetime.now(timezone.utc)
        current_month = now.date().replace(day=1)
        previous_month = (
            current_month - timedelta(days=1)
        ).replace(day=15)

        food = self.create_category("Food")
        transport = self.create_category("Transport")
        self.assertEqual(food.status_code, 201, food.json)
        self.assertEqual(transport.status_code, 201, transport.json)

        self.create_income_entry(
            30000,
            datetime.combine(previous_month, datetime.min.time(), timezone.utc),
        )
        self.create_expense_entry(
            food.json["category"]["id"],
            5000,
            datetime.combine(previous_month, datetime.min.time(), timezone.utc),
        )

        self.create_income_entry(40000, now)
        self.create_expense_entry(
            food.json["category"]["id"], 15000, now
        )
        self.create_expense_entry(
            transport.json["category"]["id"], 7000, now
        )

        # Draft spending must not appear in the trend or category totals.
        self.create_expense_entry(
            food.json["category"]["id"],
            9000,
            now,
            post=False,
        )

        snapshot = build_financial_snapshot(
            self.workspace.id,
            self.user.id,
            as_of=now,
        )

        trends = snapshot["monthly_trends"]
        self.assertEqual(len(trends), 6)

        expected_months = []
        for offset in range(5, -1, -1):
            month_index = (
                current_month.year * 12
                + current_month.month - 1 - offset
            )
            year, month_zero = divmod(month_index, 12)
            expected_months.append(
                f"{year:04d}-{month_zero + 1:02d}"
            )

        self.assertEqual(
            [item["month"] for item in trends],
            expected_months,
        )

        previous = trends[-2]
        current = trends[-1]

        self.assertEqual(previous["income_minor"], 30000)
        self.assertEqual(previous["expenses_minor"], 5000)
        self.assertEqual(previous["net_minor"], 25000)

        self.assertEqual(current["income_minor"], 40000)
        self.assertEqual(current["expenses_minor"], 22000)
        self.assertEqual(current["net_minor"], 18000)

        self.assertEqual(
            snapshot["spending_by_category"],
            [
                {"category": "Food", "amount_minor": 15000},
                {"category": "Transport", "amount_minor": 7000},
            ],
        )
        self.assertEqual(
            snapshot["top_spending_categories"],
            snapshot["spending_by_category"],
        )
        self.assertEqual(
            snapshot["summary"]["savings_rate_percentage"],
            45.0,
        )

    def test_ai_snapshot_reports_multi_category_budget_allocations(self):
        now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

        food = self.create_category("Food")
        transport = self.create_category("Transport")
        self.assertEqual(food.status_code, 201, food.json)
        self.assertEqual(transport.status_code, 201, transport.json)

        food_id = food.json["category"]["id"]
        transport_id = transport.json["category"]["id"]

        created = self.create_multi_category_budget([
            {"category_id": food_id, "amount_minor": 100000},
            {"category_id": transport_id, "amount_minor": 50000},
        ])
        self.assertEqual(created.status_code, 201, created.json)

        self.create_expense_entry(food_id, 25000, now)
        self.create_expense_entry(transport_id, 55000, now)

        snapshot = build_financial_snapshot(
            self.workspace.id,
            self.user.id,
            as_of=now,
        )

        self.assertEqual(len(snapshot["budgets"]), 1)
        budget = snapshot["budgets"][0]

        self.assertEqual(budget["amount_minor"], 150000)
        self.assertEqual(budget["actual_minor"], 80000)
        self.assertEqual(budget["remaining_minor"], 70000)
        self.assertEqual(budget["usage_percentage"], 53.33)
        self.assertFalse(budget["is_over_budget"])

        categories = {
            item["category"]: item
            for item in budget["categories"]
        }

        self.assertEqual(categories["Food"]["actual_minor"], 25000)
        self.assertEqual(categories["Food"]["remaining_minor"], 75000)
        self.assertEqual(categories["Food"]["usage_percentage"], 25.0)

        self.assertEqual(
            categories["Transport"]["actual_minor"], 55000
        )
        self.assertEqual(
            categories["Transport"]["remaining_minor"], -5000
        )
        self.assertEqual(
            categories["Transport"]["usage_percentage"], 110.0
        )
        self.assertTrue(categories["Transport"]["is_over_budget"])

    def test_ai_snapshot_category_spending_is_offset_by_reversal(self):
        now = datetime.now(timezone.utc)
        category = self.create_category("Transport")
        self.assertEqual(category.status_code, 201, category.json)

        original = self.create_expense_entry(
            category.json["category"]["id"], 25000, now
        )

        reverse_entry(
            entry_id=original.id,
            workspace_id=self.workspace.id,
            reversed_by=self.user.id,
            reference=f"AI-CAT-REV-{uuid4().hex[:12]}",
            transaction_date=now,
        )
        db.session.commit()

        snapshot = build_financial_snapshot(
            self.workspace.id,
            self.user.id,
            as_of=now,
        )

        self.assertEqual(snapshot["spending_by_category"], [])
        self.assertEqual(snapshot["top_spending_categories"], [])
        self.assertEqual(
            snapshot["monthly_trends"][-1]["expenses_minor"],
            0,
        )

    def test_ai_snapshot_savings_rate_is_none_when_income_is_zero(self):
        now = datetime.now(timezone.utc)

        snapshot = build_financial_snapshot(
            self.workspace.id,
            self.user.id,
            as_of=now,
        )

        self.assertEqual(
            snapshot["summary"]["monthly_income_minor"],
            0,
        )
        self.assertIsNone(
            snapshot["summary"]["savings_rate_percentage"]
        )

    def test_ai_snapshot_calculates_posted_financial_totals(self):
        now = datetime.now(timezone.utc)
        category = self.create_category("Food")
        self.assertEqual(category.status_code, 201, category.json)

        self.create_income_entry(40000, now)
        self.create_expense_entry(
            category.json["category"]["id"], 15000, now
        )

        snapshot = build_financial_snapshot(
            self.workspace.id, self.user.id
        )

        self.assertEqual(snapshot["currency"], "KES")
        self.assertEqual(
            snapshot["summary"]["total_assets_minor"], 125000
        )
        self.assertEqual(
            snapshot["summary"]["monthly_income_minor"], 40000
        )
        self.assertEqual(
            snapshot["summary"]["monthly_expenses_minor"], 15000
        )
        self.assertEqual(
            snapshot["summary"]["monthly_net_minor"], 25000
        )

    def test_ai_snapshot_reversal_offsets_expense(self):
        now = datetime.now(timezone.utc)
        category = self.create_category("Transport")
        self.assertEqual(category.status_code, 201, category.json)

        original = self.create_expense_entry(
            category.json["category"]["id"], 25000, now
        )

        reverse_entry(
            entry_id=original.id,
            workspace_id=self.workspace.id,
            reversed_by=self.user.id,
            reference=f"AI-REV-{uuid4().hex[:12]}",
            transaction_date=now,
        )
        db.session.commit()

        snapshot = build_financial_snapshot(
            self.workspace.id, self.user.id
        )

        self.assertEqual(
            snapshot["summary"]["monthly_expenses_minor"], 0
        )
        self.assertEqual(
            snapshot["summary"]["total_assets_minor"], 100000
        )

    def test_ai_snapshot_ignores_other_workspace_journal_entries(self):
        other_workspace = Workspace(
            name="Other AI Workspace",
            workspace_type="personal",
            functional_currency="KES",
        )
        db.session.add(other_workspace)
        db.session.flush()

        foreign_entry = JournalEntry(
            workspace_id=other_workspace.id,
            reference=f"AI-FOREIGN-{uuid4().hex[:12]}",
            description="Foreign workspace entry",
            status="posted",
            transaction_date=datetime.now(timezone.utc),
            created_by=self.user.id,
        )
        db.session.add(foreign_entry)
        db.session.flush()

        db.session.add(
            JournalLine(
                journal_entry_id=foreign_entry.id,
                ledger_account_id=self.source_ledger.id,
                currency_code="KES",
                debit_minor=500000,
                credit_minor=0,
                functional_debit_minor=500000,
                functional_credit_minor=0,
                exchange_rate=1,
            )
        )
        db.session.commit()

        snapshot = build_financial_snapshot(
            self.workspace.id, self.user.id
        )

        self.assertEqual(
            snapshot["summary"]["total_assets_minor"], 100000
        )

    def test_ai_snapshot_rejects_non_member(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        with self.assertRaises(PermissionError):
            build_financial_snapshot(
                self.workspace.id, outsider.id
            )

    def test_dashboard_calculates_posted_financial_totals(self):
        now = datetime.now(timezone.utc)
        category = self.create_category("Food")
        self.assertEqual(category.status_code, 201, category.json)

        self.create_income_entry(40000, now)
        self.create_expense_entry(
            category.json["category"]["id"], 15000, now
        )

        response = self.get_dashboard()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        summary = response.json["summary"]
        self.assertEqual(summary["total_assets_minor"], 125000)
        self.assertEqual(summary["monthly_income_minor"], 40000)
        self.assertEqual(summary["monthly_expenses_minor"], 15000)
        self.assertEqual(summary["total_savings_minor"], 0)
        self.assertEqual(response.json["currency"], "KES")
        self.assertEqual(response.json["timezone"], "UTC")

    def test_dashboard_ignores_journal_entries_from_other_workspaces(self):
        now = datetime.now(timezone.utc)

        other_workspace = Workspace(
            name="Other Workspace",
            workspace_type="personal",
            functional_currency="KES",
        )
        db.session.add(other_workspace)
        db.session.flush()

        foreign_entry = JournalEntry(
            workspace_id=other_workspace.id,
            reference=f"FOREIGN-{uuid4().hex[:12]}",
            description="Foreign workspace entry",
            status="posted",
            transaction_date=now,
            created_by=self.user.id,
        )
        db.session.add(foreign_entry)
        db.session.flush()

        # Deliberately inconsistent data: a foreign entry references
        # this workspace's asset ledger.
        db.session.add(
            JournalLine(
                journal_entry_id=foreign_entry.id,
                ledger_account_id=self.source_ledger.id,
                currency_code="KES",
                debit_minor=500000,
                credit_minor=0,
                functional_debit_minor=500000,
                functional_credit_minor=0,
                exchange_rate=1,
            )
        )
        db.session.commit()

        response = self.get_dashboard()

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(
            response.json["summary"]["total_assets_minor"],
            100000,
        )

    def test_dashboard_reversal_offsets_expense(self):
        now = datetime.now(timezone.utc)
        category = self.create_category("Transport")
        self.assertEqual(category.status_code, 201, category.json)

        original = self.create_expense_entry(
            category.json["category"]["id"], 25000, now
        )

        before = self.get_dashboard()
        self.assertEqual(before.status_code, 200, before.json)
        self.assertEqual(
            before.json["summary"]["monthly_expenses_minor"], 25000
        )

        reverse_entry(
            entry_id=original.id,
            workspace_id=self.workspace.id,
            reversed_by=self.user.id,
            reference=f"TEST-REV-{uuid4().hex[:12]}",
            transaction_date=now,
        )
        db.session.commit()

        after = self.get_dashboard()
        self.assertEqual(after.status_code, 200, after.json)
        self.assertEqual(
            after.json["summary"]["monthly_expenses_minor"], 0
        )
        self.assertEqual(
            after.json["summary"]["total_assets_minor"], 100000
        )

    def test_non_member_cannot_access_workspace_dashboard(self):
        outsider = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(outsider)
        db.session.commit()

        outsider_token = create_access_token(identity=str(outsider.id))
        response = self.client.get(
            f"/api/v1/finance/dashboard?workspace_id={self.workspace.id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        self.assertEqual(response.status_code, 404)



    def create_repayment_case(self, direction="borrowed", role="owner"):
        self.membership.role = role

        system_specs = [
            ("1200", "Accounts Receivable", "asset", "debit"),
            ("2000", "Loans and Credit Payable", "liability", "credit"),
            ("4000", "Income", "income", "credit"),
            ("5000", "Expenses", "expense", "debit"),
        ]

        existing_system_codes = {
            code
            for (code,) in db.session.query(LedgerAccount.code).filter_by(
                workspace_id=self.workspace.id
            ).all()
        }

        system_accounts = [
            LedgerAccount(
                workspace_id=self.workspace.id,
                financial_account_id=None,
                code=code,
                name=name,
                account_class=account_class,
                normal_balance=normal_balance,
                currency_code="KES",
                is_system=True,
                is_active=True,
            )
            for code, name, account_class, normal_balance in system_specs
            if code not in existing_system_codes
        ]
        db.session.add_all(system_accounts)

        today = date.today()

        loan = PersonalLoan(
            workspace_id=self.workspace.id,
            created_by=self.user.id,
            direction=direction,
            counterparty_name="Test Counterparty",
            loan_type="interest_bearing",
            principal_minor=9000,
            currency_code="KES",
            interest_method="flat",
            interest_rate_percent=Decimal("10"),
            repayment_frequency="one_time",
            term_count=1,
            installment_minor=10000,
            start_date=today,
            due_date=today + timedelta(days=30),
            status="active",
        )
        db.session.add(loan)
        db.session.flush()

        installment = LoanInstallment(
            workspace_id=self.workspace.id,
            personal_loan_id=loan.id,
            installment_number=1,
            due_date=loan.due_date,
            amount_minor=10000,
            principal_minor=9000,
            interest_minor=1000,
            paid_minor=0,
            status="scheduled",
        )
        db.session.add(installment)
        db.session.flush()

        payment = LoanPayment(
            workspace_id=self.workspace.id,
            personal_loan_id=loan.id,
            amount_minor=10000,
            principal_minor=10000,
            interest_minor=0,
            payment_date=today,
            payment_method="mobile_money",
            reference="TEST-LOAN-PAYMENT-001",
            status="pending",
            recorded_by=self.user.id,
        )
        db.session.add(payment)
        db.session.commit()

        return loan, installment, payment

    def test_borrowed_loan_repayment_posts_correct_accounting(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        confirmed = confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.principal_minor, 9000)
        self.assertEqual(confirmed.interest_minor, 1000)
        self.assertIsNotNone(confirmed.journal_entry_id)
        self.assertEqual(installment.paid_minor, 10000)
        self.assertEqual(installment.status, "paid")
        self.assertEqual(loan.status, "paid")
        self.assertEqual(self.source_balance_minor(), 90000)

        lines = JournalLine.query.filter_by(
            journal_entry_id=confirmed.journal_entry_id
        ).all()
        amounts = {
            line.ledger_account_id: (
                line.functional_debit_minor,
                line.functional_credit_minor,
            )
            for line in lines
        }

        self.assertEqual(
            amounts[ self.source_ledger.id ],
            (0, 10000),
        )

        payable = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="2000"
        ).one()
        expense = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="5000"
        ).one()

        self.assertEqual(amounts[payable.id], (9000, 0))
        self.assertEqual(amounts[expense.id], (1000, 0))

    def test_lent_loan_repayment_posts_correct_accounting(self):
        loan, installment, payment = self.create_repayment_case("lent")

        confirmed = confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.principal_minor, 9000)
        self.assertEqual(confirmed.interest_minor, 1000)
        self.assertEqual(installment.paid_minor, 10000)
        self.assertEqual(loan.status, "paid")
        self.assertEqual(self.source_balance_minor(), 110000)

        lines = JournalLine.query.filter_by(
            journal_entry_id=confirmed.journal_entry_id
        ).all()
        amounts = {
            line.ledger_account_id: (
                line.functional_debit_minor,
                line.functional_credit_minor,
            )
            for line in lines
        }

        self.assertEqual(
            amounts[self.source_ledger.id],
            (10000, 0),
        )

        receivable = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="1200"
        ).one()
        income = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id, code="4000"
        ).one()

        self.assertEqual(amounts[receivable.id], (0, 9000))
        self.assertEqual(amounts[income.id], (0, 1000))

    def test_external_borrowed_repayment_does_not_debit_registered_account(self):
        loan, installment, payment = self.create_repayment_case("borrowed")
        payment.payment_source = "external"
        db.session.commit()

        starting_balance = self.source_balance_minor()

        confirmed = confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=None,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertIsNone(confirmed.financial_account_id)
        self.assertEqual(self.source_balance_minor(), starting_balance)

        clearing = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id,
            code="LN-EXT-PAY-CLEARING-KES",
        ).one()

        clearing_line = JournalLine.query.filter_by(
            journal_entry_id=confirmed.journal_entry_id,
            ledger_account_id=clearing.id,
        ).one()

        self.assertEqual(clearing_line.functional_credit_minor, 10000)

    def test_external_lent_loan_receipt_does_not_credit_registered_account(self):
        loan, installment, payment = self.create_repayment_case("lent")
        payment.payment_source = "external"
        db.session.commit()

        starting_balance = self.source_balance_minor()

        confirmed = confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=None,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertIsNone(confirmed.financial_account_id)
        self.assertEqual(self.source_balance_minor(), starting_balance)

        clearing = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id,
            code="LN-EXT-REC-CLEARING-KES",
        ).one()

        clearing_line = JournalLine.query.filter_by(
            journal_entry_id=confirmed.journal_entry_id,
            ledger_account_id=clearing.id,
        ).one()

        self.assertEqual(clearing_line.functional_debit_minor, 10000)

    def test_borrowed_repayment_rejects_insufficient_registered_funds(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        opening_line = JournalLine.query.filter_by(
            ledger_account_id=self.source_ledger.id
        ).one()

        equity_line = JournalLine.query.filter_by(
            journal_entry_id=opening_line.journal_entry_id,
            ledger_account_id=self.equity_ledger.id,
        ).one()

        # Keep the opening journal balanced at KES 50.00.
        opening_line.debit_minor = 5000
        opening_line.functional_debit_minor = 5000
        equity_line.credit_minor = 5000
        equity_line.functional_credit_minor = 5000
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Insufficient funds"):
            confirm_loan_payment(
                payment_id=payment.id,
                workspace_id=self.workspace.id,
                confirmed_by=self.user.id,
                financial_account_id=self.source_account.id,
            )

        db.session.rollback()
        db.session.refresh(payment)
        db.session.refresh(installment)

        self.assertEqual(payment.status, "pending")
        self.assertIsNone(payment.journal_entry_id)
        self.assertEqual(installment.paid_minor, 0)
        self.assertEqual(self.source_balance_minor(), 5000)

    def test_registered_cash_account_can_be_used_for_mobile_money_payment(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        # Payment method and registered account type are independent.
        self.source_account.account_type = "cash"
        payment.payment_method = "mobile_money"
        db.session.commit()

        confirmed = confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.payment_method, "mobile_money")
        self.assertEqual(self.source_balance_minor(), 90000)


    def test_partial_repayment_allocates_interest_before_principal(self):
        installment = SimpleNamespace(
            id=uuid4(),
            due_date=date.today(),
            installment_number=1,
            status="scheduled",
            principal_minor=9000,
            interest_minor=1000,
        )

        allocations = build_payment_allocations(
            [installment], [], 1500
        )

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0]["amount_minor"], 1500)
        self.assertEqual(allocations[0]["interest_minor"], 1000)
        self.assertEqual(allocations[0]["principal_minor"], 500)

    def test_repayment_allocates_oldest_installment_first(self):
        today = date.today()
        older = SimpleNamespace(
            id=uuid4(),
            due_date=today - timedelta(days=30),
            installment_number=1,
            status="scheduled",
            principal_minor=4000,
            interest_minor=1000,
        )
        newer = SimpleNamespace(
            id=uuid4(),
            due_date=today + timedelta(days=30),
            installment_number=2,
            status="scheduled",
            principal_minor=8000,
            interest_minor=1000,
        )

        allocations = build_payment_allocations(
            [newer, older], [], 6000
        )

        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0]["installment_id"], older.id)
        self.assertEqual(allocations[0]["amount_minor"], 5000)
        self.assertEqual(allocations[1]["installment_id"], newer.id)
        self.assertEqual(allocations[1]["amount_minor"], 1000)
        self.assertEqual(allocations[1]["interest_minor"], 1000)

    def test_repayment_rejects_amount_above_outstanding_balance(self):
        installment = SimpleNamespace(
            id=uuid4(),
            due_date=date.today(),
            installment_number=1,
            status="scheduled",
            principal_minor=9000,
            interest_minor=1000,
        )

        with self.assertRaisesRegex(ValueError, "exceeds"):
            build_payment_allocations(
                [installment], [], 10001
            )

    def test_partial_loan_repayment_updates_balance_and_accounting(self):
        loan, installment, original_payment = self.create_repayment_case(
            "borrowed"
        )

        original_payment.amount_minor = 1500
        original_payment.principal_minor = 1500
        original_payment.interest_minor = 0
        db.session.commit()

        confirmed = confirm_loan_payment(
            payment_id=original_payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.principal_minor, 500)
        self.assertEqual(confirmed.interest_minor, 1000)
        self.assertEqual(installment.paid_minor, 1500)
        self.assertEqual(installment.status, "partially_paid")
        self.assertEqual(loan.status, "active")
        self.assertEqual(
            installment.amount_minor - installment.paid_minor,
            8500,
        )
        self.assertEqual(self.source_balance_minor(), 98500)

    def test_viewer_cannot_confirm_loan_repayment(self):
        loan, installment, payment = self.create_repayment_case(
            "borrowed", role="viewer"
        )

        with self.assertRaisesRegex(ValueError, "Viewers cannot"):
            confirm_loan_payment(
                payment_id=payment.id,
                workspace_id=self.workspace.id,
                confirmed_by=self.user.id,
                financial_account_id=self.source_account.id,
            )

        db.session.rollback()
        db.session.refresh(payment)
        db.session.refresh(installment)

        self.assertEqual(payment.status, "pending")
        self.assertIsNone(payment.journal_entry_id)
        self.assertEqual(installment.paid_minor, 0)



    def test_pending_loan_repayment_can_be_edited(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        response = self.client.patch(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "amount_minor": 8500,
                "payment_date": date.today().isoformat(),
                "payment_method": "cash",
                "reference": "CORRECTED-REF",
                "notes": "Corrected pending repayment",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        db.session.refresh(payment)
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.amount_minor, 8500)
        self.assertEqual(payment.principal_minor, 8500)
        self.assertEqual(payment.interest_minor, 0)
        self.assertEqual(payment.payment_method, "cash")
        self.assertEqual(payment.reference, "CORRECTED-REF")
        self.assertEqual(payment.notes, "Corrected pending repayment")
        self.assertIsNone(payment.financial_account_id)
        self.assertIsNone(payment.journal_entry_id)
        self.assertEqual(installment.paid_minor, 0)

        allocation_count = LoanPaymentAllocation.query.filter_by(
            payment_id=payment.id
        ).count()
        self.assertEqual(allocation_count, 0)

    def test_pending_loan_repayment_edit_rejects_amount_above_outstanding(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        response = self.client.patch(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "amount_minor": 10001,
                "payment_date": date.today().isoformat(),
                "payment_method": "mobile_money",
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        db.session.refresh(payment)
        self.assertEqual(payment.amount_minor, 10000)
        self.assertEqual(payment.status, "pending")

    def test_pending_loan_repayment_can_be_deleted(self):
        loan, installment, payment = self.create_repayment_case("borrowed")
        payment_id = payment.id

        response = self.client.delete(
            f"/api/v1/loans/{loan.id}/payments/{payment_id}",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])

        self.assertIsNone(
            LoanPayment.query.filter_by(
                id=payment_id,
                workspace_id=self.workspace.id,
            ).first()
        )
        self.assertEqual(installment.paid_minor, 0)

        self.assertEqual(
            LoanPaymentAllocation.query.filter_by(
                payment_id=payment_id
            ).count(),
            0,
        )

    def test_confirmed_loan_repayment_cannot_be_edited(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        response = self.client.patch(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "amount_minor": 5000,
                "payment_date": date.today().isoformat(),
                "payment_method": "cash",
            },
        )

        self.assertEqual(response.status_code, 409, response.json)
        db.session.refresh(payment)
        self.assertEqual(payment.status, "confirmed")
        self.assertIsNotNone(payment.journal_entry_id)

    def test_confirmed_loan_repayment_cannot_be_deleted(self):
        loan, installment, payment = self.create_repayment_case("borrowed")
        payment_id = payment.id

        confirm_loan_payment(
            payment_id=payment.id,
            workspace_id=self.workspace.id,
            confirmed_by=self.user.id,
            financial_account_id=self.source_account.id,
        )
        db.session.commit()

        response = self.client.delete(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )

        self.assertEqual(response.status_code, 409, response.json)

        db.session.refresh(payment)
        self.assertEqual(payment.status, "confirmed")
        self.assertIsNotNone(payment.journal_entry_id)
        self.assertIsNotNone(
            LoanPayment.query.filter_by(id=payment_id).first()
        )

    def test_viewer_cannot_edit_or_delete_pending_loan_repayment(self):
        loan, installment, payment = self.create_repayment_case(
            "borrowed", role="viewer"
        )

        payload = {
            "workspace_id": str(self.workspace.id),
            "amount_minor": 8000,
            "payment_date": date.today().isoformat(),
            "payment_method": "mobile_money",
        }

        edit_response = self.client.patch(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(edit_response.status_code, 403)

        delete_response = self.client.delete(
            f"/api/v1/loans/{loan.id}/payments/{payment.id}",
            headers=self.headers,
            json={"workspace_id": str(self.workspace.id)},
        )
        self.assertEqual(delete_response.status_code, 403)

        db.session.refresh(payment)
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.amount_minor, 10000)


    def test_get_personal_loan_returns_details_and_history(self):
        loan, installment, payment = self.create_repayment_case("borrowed")

        response = self.client.get(
            f"/api/v1/loans/{loan.id}?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["loan"]["id"], str(loan.id))
        self.assertIsNone(response.json["disbursement"])

        self.assertEqual(len(response.json["installments"]), 1)
        self.assertEqual(
            response.json["installments"][0]["id"],
            str(installment.id),
        )

        self.assertEqual(len(response.json["payments"]), 1)
        self.assertEqual(
            response.json["payments"][0]["id"],
            str(payment.id),
        )
        self.assertEqual(response.json["payments"][0]["status"], "pending")

    def test_get_personal_loan_returns_404_for_unknown_loan(self):
        response = self.client.get(
            f"/api/v1/loans/{uuid4()}?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["success"])

    def test_get_personal_loan_cannot_read_another_workspaces_loan(self):
        foreign_user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        foreign_workspace = Workspace(
            name="Foreign Workspace",
            workspace_type="personal",
            functional_currency="KES",
        )
        db.session.add_all([foreign_user, foreign_workspace])
        db.session.flush()

        db.session.add(WorkspaceMember(
            workspace_id=foreign_workspace.id,
            user_id=foreign_user.id,
            role="owner",
        ))
        db.session.flush()

        foreign_loan = PersonalLoan(
            workspace_id=foreign_workspace.id,
            created_by=foreign_user.id,
            direction="lent",
            counterparty_name="Foreign Counterparty",
            loan_type="personal",
            principal_minor=5000,
            currency_code="KES",
            interest_method="none",
            interest_rate_percent=Decimal("0"),
            repayment_frequency="one_time",
            term_count=1,
            start_date=date.today(),
            due_date=date.today() + timedelta(days=30),
            status="draft",
        )
        db.session.add(foreign_loan)
        db.session.commit()

        response = self.client.get(
            f"/api/v1/loans/{foreign_loan.id}?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json["success"])


    def ensure_accounts_payable_ledger(self):
        ledger = LedgerAccount.query.filter_by(
            workspace_id=self.workspace.id,
            code="2100",
        ).first()

        if ledger:
            return ledger

        ledger = LedgerAccount(
            workspace_id=self.workspace.id,
            financial_account_id=None,
            code="2100",
            name="Accounts Payable",
            account_class="liability",
            normal_balance="credit",
            currency_code="KES",
            is_system=True,
            is_active=True,
        )
        db.session.add(ledger)
        db.session.commit()
        return ledger

    def create_bill(self, amount=40000):
        category = self.create_category("Bills Expense")
        self.assertEqual(category.status_code, 201, category.json)
        self.ensure_accounts_payable_ledger()

        response = self.client.post(
            "/api/v1/bills",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "category_id": category.json["category"]["id"],
                "payee_name": "Electricity Provider",
                "title": "Monthly Electricity",
                "amount_minor": amount,
                "bill_date": date.today().isoformat(),
                "due_date": (date.today() + timedelta(days=7)).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 201, response.json)
        return response, category.json["category"]

    def pay_bill(self, bill_id, amount):
        return self.client.post(
            f"/api/v1/bills/{bill_id}/payments",
            headers=self.headers,
            json={
                "workspace_id": str(self.workspace.id),
                "financial_account_id": str(self.source_account.id),
                "amount_minor": amount,
                "payment_date": date.today().isoformat(),
            },
        )

    def test_create_bill_posts_expense_without_reducing_wallet(self):
        opening_balance = self.source_balance_minor()
        response, category = self.create_bill(25000)

        self.assertEqual(response.status_code, 201, response.json)
        bill = response.json["bill"]

        self.assertEqual(bill["amount_minor"], 25000)
        self.assertEqual(bill["amount_paid_minor"], 0)
        self.assertEqual(bill["remaining_minor"], 25000)
        self.assertEqual(bill["status"], "unpaid")
        self.assertEqual(self.source_balance_minor(), opening_balance)

        entry = JournalEntry.query.filter_by(
            id=UUID(bill["journal_entry_id"]),
            workspace_id=self.workspace.id,
        ).one()
        self.assertEqual(entry.status, "posted")

        lines = JournalLine.query.filter_by(
            journal_entry_id=entry.id,
        ).all()

        expense = LedgerAccount.query.filter_by(
            id=UUID(category["ledger_account"]["id"]),
        ).one()
        payable = self.ensure_accounts_payable_ledger()

        self.assertTrue(any(
            line.ledger_account_id == expense.id
            and line.functional_debit_minor == 25000
            for line in lines
        ))
        self.assertTrue(any(
            line.ledger_account_id == payable.id
            and line.functional_credit_minor == 25000
            for line in lines
        ))

    def test_partial_and_full_bill_payments(self):
        opening_balance = self.source_balance_minor()
        created, _ = self.create_bill(40000)
        bill_id = created.json["bill"]["id"]

        partial = self.pay_bill(bill_id, 15000)
        self.assertEqual(partial.status_code, 201, partial.json)
        self.assertEqual(partial.json["bill"]["amount_paid_minor"], 15000)
        self.assertEqual(partial.json["bill"]["remaining_minor"], 25000)
        self.assertEqual(partial.json["bill"]["status"], "partially_paid")
        self.assertEqual(self.source_balance_minor(), opening_balance - 15000)

        final = self.pay_bill(bill_id, 25000)
        self.assertEqual(final.status_code, 201, final.json)
        self.assertEqual(final.json["bill"]["amount_paid_minor"], 40000)
        self.assertEqual(final.json["bill"]["remaining_minor"], 0)
        self.assertEqual(final.json["bill"]["status"], "paid")
        self.assertEqual(self.source_balance_minor(), opening_balance - 40000)

        self.assertEqual(
            BillPayment.query.filter_by(
                bill_id=UUID(bill_id),
                workspace_id=self.workspace.id,
            ).count(),
            2,
        )

    def test_reversed_bill_payment_no_longer_counts_toward_paid_total(self):
        opening_balance = self.source_balance_minor()
        created, _ = self.create_bill(100000)
        bill_id = created.json["bill"]["id"]

        first_payment = self.pay_bill(bill_id, 65000)
        self.assertEqual(first_payment.status_code, 201, first_payment.json)

        second_payment = self.pay_bill(bill_id, 5000)
        self.assertEqual(second_payment.status_code, 201, second_payment.json)

        first_payment_id = UUID(first_payment.json["payment"]["id"])
        original_entry = JournalEntry.query.filter_by(
            id=UUID(first_payment.json["payment"]["journal_entry_id"]),
            workspace_id=self.workspace.id,
        ).one()

        reversal = reverse_entry(
            entry_id=original_entry.id,
            workspace_id=self.workspace.id,
            reversed_by=self.user.id,
            reference=f"TEST-BILL-REV-{uuid4().hex[:12]}",
            transaction_date=datetime.now(timezone.utc),
        )
        db.session.commit()

        self.assertEqual(original_entry.status, "reversed")
        self.assertEqual(reversal.status, "posted")

        response = self.client.get(
            f"/api/v1/bills/{bill_id}?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)

        bill = response.json["bill"]
        self.assertEqual(bill["amount_paid_minor"], 5000)
        self.assertEqual(bill["remaining_minor"], 95000)
        self.assertEqual(bill["status"], "partially_paid")

        payments = {
            UUID(payment["id"]): payment
            for payment in bill["payments"]
        }

        self.assertEqual(len(payments), 2)
        self.assertEqual(payments[first_payment_id]["status"], "reversed")
        self.assertEqual(
            payments[UUID(second_payment.json["payment"]["id"])]["status"],
            "posted",
        )

        self.assertEqual(
            self.source_balance_minor(),
            opening_balance - 5000,
        )

        rejected = self.pay_bill(bill_id, 95001)
        self.assertEqual(rejected.status_code, 400, rejected.json)

        accepted = self.pay_bill(bill_id, 95000)
        self.assertEqual(accepted.status_code, 201, accepted.json)
        self.assertEqual(accepted.json["bill"]["amount_paid_minor"], 100000)
        self.assertEqual(accepted.json["bill"]["remaining_minor"], 0)

    def test_bill_rejects_payment_above_outstanding_balance(self):
        created, _ = self.create_bill(20000)
        bill_id = created.json["bill"]["id"]

        response = self.pay_bill(bill_id, 20001)

        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(
            BillPayment.query.filter_by(
                bill_id=UUID(bill_id),
                workspace_id=self.workspace.id,
            ).count(),
            0,
        )

    def _set_test_password(self, password="CurrentTestPassword123"):
        self.user.password_hash = generate_password_hash(password)
        db.session.commit()
        return password

    def test_change_password_success_revokes_all_sessions(self):
        current_password = self._set_test_password()
        _, access_token, _ = self._create_session_tokens()
        _, other_access_token, _ = self._create_session_tokens()

        response = self.client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "current_password": current_password,
                "new_password": "NewSecurePassword456",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(check_password_hash(
            self.user.password_hash, "NewSecurePassword456"
        ))

        active_tokens = db.session.query(RefreshToken).filter(
            RefreshToken.user_id == self.user.id,
            RefreshToken.revoked_at.is_(None),
        ).count()
        self.assertEqual(active_tokens, 0)

        for token in (access_token, other_access_token):
            protected = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(protected.status_code, 401)

        audit = AuditLog.query.filter_by(
            user_id=self.user.id,
            action="password_changed",
        ).first()
        self.assertIsNotNone(audit)

    def test_change_password_rejects_incorrect_current_password(self):
        self._set_test_password()

        response = self.client.post(
            "/api/v1/auth/change-password",
            headers=self.headers,
            json={
                "current_password": "WrongCurrentPassword123",
                "new_password": "NewSecurePassword456",
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertTrue(check_password_hash(
            self.user.password_hash, "CurrentTestPassword123"
        ))

    def test_change_password_enforces_existing_length_policy(self):
        current_password = self._set_test_password()

        for new_password in ("TooShort1", "N" * 129):
            response = self.client.post(
                "/api/v1/auth/change-password",
                headers=self.headers,
                json={
                    "current_password": current_password,
                    "new_password": new_password,
                },
            )
            self.assertEqual(response.status_code, 400, response.json)

        self.assertTrue(check_password_hash(
            self.user.password_hash, current_password
        ))

    def test_change_password_rejects_reusing_current_password(self):
        current_password = self._set_test_password()

        response = self.client.post(
            "/api/v1/auth/change-password",
            headers=self.headers,
            json={
                "current_password": current_password,
                "new_password": current_password,
            },
        )

        self.assertEqual(response.status_code, 400, response.json)
        self.assertTrue(check_password_hash(
            self.user.password_hash, current_password
        ))

    def _create_session_tokens(self, session_id=None):
        session_id = session_id or uuid4()
        claims = {"sid": str(session_id)}

        access_token = create_access_token(
            identity=str(self.user.id),
            additional_claims=claims,
        )
        refresh_token = create_refresh_token(
            identity=str(self.user.id),
            additional_claims=claims,
        )

        now = datetime.now(timezone.utc)
        db.session.add(RefreshToken(
            user_id=self.user.id,
            token_hash=hashlib.sha256(
                refresh_token.encode("utf-8")
            ).hexdigest(),
            session_id=session_id,
            user_agent="PesaPilot test device",
            ip_address="127.0.0.1",
            expires_at=now + timedelta(days=30),
        ))
        db.session.commit()

        return session_id, access_token, refresh_token

    def test_active_sessions_lists_current_session(self):
        session_id, access_token, _ = self._create_session_tokens()

        response = self.client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["count"], 1)
        self.assertEqual(
            response.json["sessions"][0]["session_id"],
            str(session_id),
        )
        self.assertTrue(response.json["sessions"][0]["is_current"])

    def test_revoking_session_invalidates_its_access_token(self):
        _, current_access_token, _ = self._create_session_tokens()
        session_id, target_access_token, _ = self._create_session_tokens()

        response = self.client.delete(
            f"/api/v1/auth/sessions/{session_id}",
            headers={
                "Authorization": f"Bearer {current_access_token}",
            },
        )

        self.assertEqual(response.status_code, 200, response.json)

        protected_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {target_access_token}"},
        )
        self.assertEqual(protected_response.status_code, 401)

    def test_revoke_others_preserves_current_session(self):
        current_id, current_access, _ = self._create_session_tokens()
        other_id, other_access, _ = self._create_session_tokens()

        response = self.client.post(
            "/api/v1/auth/sessions/revoke-others",
            headers={"Authorization": f"Bearer {current_access}"},
        )

        self.assertEqual(response.status_code, 200, response.json)

        current_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {current_access}"},
        )
        other_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {other_access}"},
        )

        self.assertEqual(current_response.status_code, 200)
        self.assertEqual(other_response.status_code, 401)

        sessions_response = self.client.get(
            "/api/v1/auth/sessions",
            headers={"Authorization": f"Bearer {current_access}"},
        )
        self.assertEqual(sessions_response.status_code, 200)
        self.assertEqual(
            [item["session_id"] for item in sessions_response.json["sessions"]],
            [str(current_id)],
        )

    def test_logout_revokes_entire_current_session(self):
        session_id, access_token, refresh_token = self._create_session_tokens()

        # Add a second refresh-token record for the same session,
        # representing a previous token created during rotation.
        second_raw_token = create_refresh_token(
            identity=str(self.user.id),
            additional_claims={"sid": str(session_id)},
        )
        now = datetime.now(timezone.utc)
        db.session.add(RefreshToken(
            user_id=self.user.id,
            token_hash=hashlib.sha256(
                second_raw_token.encode("utf-8")
            ).hexdigest(),
            session_id=session_id,
            user_agent="PesaPilot test device",
            ip_address="127.0.0.1",
            expires_at=now + timedelta(days=30),
        ))
        db.session.commit()

        response = self.client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        self.assertEqual(response.status_code, 200, response.json)

        remaining_active = db.session.query(RefreshToken).filter(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        ).count()
        self.assertEqual(remaining_active, 0)

        protected_response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(protected_response.status_code, 401)


class AIManagerRulesEngineTestCase(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "as_of": "2026-11-16",
            "currency": "KES",
            "summary": {
                "monthly_income_minor": 100000,
                "monthly_expenses_minor": 80000,
                "monthly_net_minor": 20000,
            },
            "budgets": [],
            "bills": [],
            "savings_goals": [],
            "loans": [],
        }

    def get_rules(self, insights):
        return {item["rule"] for item in insights}

    def test_negative_cash_flow_generates_high_severity_insight(self):
        self.snapshot["summary"]["monthly_income_minor"] = 50000
        self.snapshot["summary"]["monthly_expenses_minor"] = 70000

        insights = generate_insights(self.snapshot)

        cash_flow = next(
            item for item in insights
            if item["rule"] == "negative_monthly_cash_flow"
        )

        self.assertEqual(cash_flow["severity"], "high")
        self.assertEqual(cash_flow["amount_minor"], 20000)

    def test_budget_warning_and_exceeded_thresholds(self):
        self.snapshot["budgets"] = [
            {
                "id": "budget-80",
                "category": "Food",
                "amount_minor": 100000,
                "actual_minor": 80000,
                "currency": "KES",
            },
            {
                "id": "budget-100",
                "category": "Transport",
                "amount_minor": 50000,
                "actual_minor": 55000,
                "currency": "KES",
            },
        ]

        insights = generate_insights(self.snapshot)

        self.assertIn("budget_near_limit", self.get_rules(insights))
        self.assertIn("budget_exceeded", self.get_rules(insights))

        exceeded = next(
            item for item in insights
            if item["rule"] == "budget_exceeded"
        )
        self.assertEqual(exceeded["amount_minor"], 5000)
        self.assertEqual(exceeded["severity"], "high")

    def test_budget_below_warning_threshold_generates_no_alert(self):
        self.snapshot["budgets"] = [{
            "id": "budget-79",
            "category": "Food",
            "amount_minor": 100000,
            "actual_minor": 79000,
            "currency": "KES",
        }]

        insights = generate_insights(self.snapshot)

        self.assertNotIn("budget_near_limit", self.get_rules(insights))
        self.assertNotIn("budget_exceeded", self.get_rules(insights))

    def test_multi_category_budget_generates_category_specific_insights(self):
        self.snapshot["budgets"] = [{
            "id": "multi-budget",
            "categories": [
                {
                    "category": "Food",
                    "amount_minor": 100000,
                    "actual_minor": 85000,
                },
                {
                    "category": "Transport",
                    "amount_minor": 50000,
                    "actual_minor": 55000,
                },
                {
                    "category": "Rent",
                    "amount_minor": 100000,
                    "actual_minor": 30000,
                },
            ],
            "currency": "KES",
        }]

        insights = generate_insights(self.snapshot)

        food = next(
            item for item in insights
            if item["title"] == "Budget approaching its limit: Food"
        )
        transport = next(
            item for item in insights
            if item["title"] == "Budget exceeded: Transport"
        )

        self.assertEqual(food["rule"], "budget_near_limit")
        self.assertEqual(food["amount_minor"], 15000)
        self.assertEqual(transport["rule"], "budget_exceeded")
        self.assertEqual(transport["amount_minor"], 5000)

        self.assertFalse(any(
            item.get("title", "").endswith(": Rent")
            for item in insights
        ))

    def test_bill_due_date_boundaries(self):
        self.snapshot["bills"] = [
            {
                "id": "overdue",
                "title": "Rent",
                "remaining_minor": 30000,
                "due_date": "2026-11-15",
                "currency": "KES",
            },
            {
                "id": "today",
                "title": "Internet",
                "remaining_minor": 5000,
                "due_date": "2026-11-16",
                "currency": "KES",
            },
            {
                "id": "soon",
                "title": "Electricity",
                "remaining_minor": 7000,
                "due_date": "2026-11-23",
                "currency": "KES",
            },
            {
                "id": "later",
                "title": "Insurance",
                "remaining_minor": 9000,
                "due_date": "2026-11-24",
                "currency": "KES",
            },
            {
                "id": "paid",
                "title": "Paid bill",
                "remaining_minor": 0,
                "due_date": "2026-11-15",
                "currency": "KES",
            },
        ]

        insights = generate_insights(self.snapshot)

        self.assertIn("bill_overdue", self.get_rules(insights))
        self.assertIn("bill_due_today", self.get_rules(insights))
        self.assertIn("bill_due_soon", self.get_rules(insights))

        bill_ids = {
            item["entity_id"] for item in insights
            if item["entity_type"] == "bill"
        }
        self.assertEqual(bill_ids, {"overdue", "today", "soon"})

    def test_savings_goal_target_dates(self):
        self.snapshot["savings_goals"] = [
            {
                "id": "missed",
                "name": "Emergency fund",
                "status": "active",
                "remaining_minor": 50000,
                "target_date": "2026-11-15",
                "currency": "KES",
            },
            {
                "id": "approaching",
                "name": "Laptop",
                "status": "active",
                "remaining_minor": 30000,
                "target_date": "2026-12-01",
                "currency": "KES",
            },
            {
                "id": "paused",
                "name": "Holiday",
                "status": "paused",
                "remaining_minor": 20000,
                "target_date": "2026-11-20",
                "currency": "KES",
            },
        ]

        insights = generate_insights(self.snapshot)

        self.assertIn("savings_target_missed", self.get_rules(insights))
        self.assertIn(
            "savings_target_approaching", self.get_rules(insights)
        )

        goal_ids = {
            item["entity_id"] for item in insights
            if item["entity_type"] == "savings_goal"
        }
        self.assertEqual(goal_ids, {"missed", "approaching"})

    def test_overdue_borrowed_and_lent_loan_installments(self):
        self.snapshot["loans"] = [
            {
                "id": "borrowed-loan",
                "direction": "borrowed",
                "overdue_installments_minor": 12000,
                "currency": "KES",
            },
            {
                "id": "lent-loan",
                "direction": "lent",
                "overdue_installments_minor": 8000,
                "currency": "KES",
            },
            {
                "id": "current-loan",
                "direction": "borrowed",
                "overdue_installments_minor": 0,
                "currency": "KES",
            },
        ]

        insights = generate_insights(self.snapshot)

        loan_insights = [
            item for item in insights
            if item["rule"] == "loan_installments_overdue"
        ]

        self.assertEqual(len(loan_insights), 2)
        self.assertEqual(
            {item["entity_id"] for item in loan_insights},
            {"borrowed-loan", "lent-loan"},
        )
        self.assertTrue(all(
            item["severity"] == "high" for item in loan_insights
        ))

    def test_insights_are_sorted_by_severity(self):
        self.snapshot["summary"]["monthly_income_minor"] = 10000
        self.snapshot["summary"]["monthly_expenses_minor"] = 20000
        self.snapshot["bills"] = [{
            "id": "overdue",
            "title": "Rent",
            "remaining_minor": 1000,
            "due_date": "2026-11-10",
            "currency": "KES",
        }]
        self.snapshot["budgets"] = [{
            "id": "budget",
            "category": "Food",
            "amount_minor": 10000,
            "actual_minor": 8500,
            "currency": "KES",
        }]

        insights = generate_insights(self.snapshot)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        positions = [
            severity_order[item["severity"]]
            for item in insights
        ]

        self.assertEqual(positions, sorted(positions))

    def test_low_savings_rate_generates_medium_insight(self):
        self.snapshot["summary"]["savings_rate_percentage"] = 5.0

        insights = generate_insights(self.snapshot)

        insight = next(
            item for item in insights
            if item["rule"] == "low_savings_rate"
        )
        self.assertEqual(insight["severity"], "medium")
        self.assertEqual(insight["entity_type"], "cash_flow")

    def test_zero_income_does_not_generate_low_savings_rate(self):
        self.snapshot["summary"]["monthly_income_minor"] = 0
        self.snapshot["summary"]["monthly_expenses_minor"] = 0
        self.snapshot["summary"]["savings_rate_percentage"] = None

        insights = generate_insights(self.snapshot)

        self.assertNotIn(
            "low_savings_rate", self.get_rules(insights)
        )

    def test_recurring_negative_cash_flow_generates_high_insight(self):
        self.snapshot["monthly_trends"] = [
            {"month": "2026-06", "net_minor": -1000},
            {"month": "2026-07", "net_minor": 500},
            {"month": "2026-08", "net_minor": -2000},
            {"month": "2026-09", "net_minor": 300},
            {"month": "2026-10", "net_minor": -500},
            {"month": "2026-11", "net_minor": 1000},
        ]

        insights = generate_insights(self.snapshot)

        insight = next(
            item for item in insights
            if item["rule"] == "recurring_negative_cash_flow"
        )
        self.assertEqual(insight["severity"], "high")
        self.assertIn("3 of the last 6 months", insight["message"])

    def test_two_negative_months_do_not_trigger_recurring_alert(self):
        self.snapshot["monthly_trends"] = [
            {"month": "2026-06", "net_minor": -1000},
            {"month": "2026-07", "net_minor": 500},
            {"month": "2026-08", "net_minor": 2000},
            {"month": "2026-09", "net_minor": 300},
            {"month": "2026-10", "net_minor": -500},
            {"month": "2026-11", "net_minor": 1000},
        ]

        insights = generate_insights(self.snapshot)

        self.assertNotIn(
            "recurring_negative_cash_flow", self.get_rules(insights)
        )

    def test_spending_concentration_generates_medium_insight(self):
        self.snapshot["spending_by_category"] = [
            {"category": "Housing", "amount_minor": 50000},
            {"category": "Food", "amount_minor": 30000},
        ]

        insights = generate_insights(self.snapshot)

        insight = next(
            item for item in insights
            if item["rule"] == "spending_concentration"
        )
        self.assertEqual(insight["severity"], "medium")
        self.assertEqual(insight["amount_minor"], 50000)
        self.assertEqual(insight["entity_type"], "spending_category")

    def test_spending_below_concentration_threshold_has_no_alert(self):
        self.snapshot["summary"]["monthly_expenses_minor"] = 100000
        self.snapshot["spending_by_category"] = [
            {"category": "Housing", "amount_minor": 39000},
            {"category": "Food", "amount_minor": 41000},
        ]

        insights = generate_insights(self.snapshot)

        self.assertNotIn(
            "spending_concentration", self.get_rules(insights)
        )

    def test_empty_snapshot_produces_no_insights(self):
        insights = generate_insights({
            "as_of": "2026-11-16",
            "currency": "KES",
            "summary": {},
            "budgets": [],
            "bills": [],
            "savings_goals": [],
            "loans": [],
        })

        self.assertEqual(insights, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class AIManagerSanitizationTestCase(unittest.TestCase):
    def test_income_and_spending_intelligence_is_sanitized(self):
        snapshot = {
            "currency": "KES",
            "minor_unit": 2,
            "summary": {
                "monthly_income_minor": 100000,
                "monthly_expenses_minor": 45000,
                "savings_rate_percentage": 55.0,
                "private_field": "must not leave the app",
            },
            "monthly_trends": [
                {
                    "month": "2026-10",
                    "income_minor": 80000,
                    "expenses_minor": 40000,
                    "net_minor": 40000,
                    "private_note": "secret",
                },
                {
                    "month": "2026-11",
                    "income_minor": 100000,
                    "expenses_minor": 45000,
                    "net_minor": 55000,
                },
            ],
            "spending_by_category": [
                {
                    "category": "Private Rent",
                    "amount_minor": 30000,
                    "category_id": "private-category-id",
                },
                {
                    "category": "Personal Medical Costs",
                    "amount_minor": 15000,
                },
            ],
            "top_spending_categories": [
                {
                    "category": "Private Rent",
                    "amount_minor": 30000,
                },
            ],
        }

        sanitized = sanitize_financial_context(snapshot)

        self.assertEqual(
            sanitized["summary"]["savings_rate_percentage"], 55.0
        )
        self.assertEqual(len(sanitized["monthly_trends"]), 2)
        self.assertEqual(
            sanitized["monthly_trends"][1]["net_minor"], 55000
        )
        self.assertEqual(
            sanitized["spending_by_category"],
            [
                {"category": "Category 1", "amount_minor": 30000},
                {"category": "Category 2", "amount_minor": 15000},
            ],
        )
        self.assertEqual(
            sanitized["top_spending_categories"],
            [{"category": "Category 1", "amount_minor": 30000}],
        )

        serialized = str(sanitized)
        self.assertNotIn("Private Rent", serialized)
        self.assertNotIn("Personal Medical Costs", serialized)
        self.assertNotIn("private-category-id", serialized)
        self.assertNotIn("must not leave the app", serialized)
        self.assertNotIn("secret", serialized)

    def test_sanitizer_anonymizes_multi_category_budget_allocations(self):
        snapshot = {
            "spending_by_category": [
                {"category": "Private Food", "amount_minor": 20000},
            ],
            "budgets": [{
                "id": "private-budget-id",
                "category": "Private Food",
                "categories": [
                    {
                        "category": "Private Food",
                        "amount_minor": 50000,
                        "actual_minor": 20000,
                        "category_id": "private-food-id",
                    },
                    {
                        "category": "Private Transport",
                        "amount_minor": 30000,
                        "actual_minor": 10000,
                        "category_id": "private-transport-id",
                    },
                ],
                "amount_minor": 80000,
                "actual_minor": 30000,
                "currency": "KES",
            }],
        }

        sanitized = sanitize_financial_context(snapshot)
        budget = sanitized["budgets"][0]

        self.assertEqual(budget["category"], "Category 1")
        self.assertEqual(
            [item["category"] for item in budget["categories"]],
            ["Category 1", "Category 2"],
        )
        self.assertEqual(
            [item["amount_minor"] for item in budget["categories"]],
            [50000, 30000],
        )

        serialized = str(sanitized)
        for private_value in (
            "Private Food",
            "Private Transport",
            "private-budget-id",
            "private-food-id",
            "private-transport-id",
        ):
            self.assertNotIn(private_value, serialized)

    def test_sanitizer_limits_monthly_trends_to_six_records(self):
        snapshot = {
            "monthly_trends": [
                {
                    "month": f"2026-{month:02d}",
                    "income_minor": 1000,
                    "expenses_minor": 500,
                    "net_minor": 500,
                }
                for month in range(1, 9)
            ],
        }

        sanitized = sanitize_financial_context(snapshot)

        self.assertEqual(len(sanitized["monthly_trends"]), 6)
        self.assertEqual(
            sanitized["monthly_trends"][0]["month"], "2026-01"
        )
        self.assertEqual(
            sanitized["monthly_trends"][-1]["month"], "2026-06"
        )
