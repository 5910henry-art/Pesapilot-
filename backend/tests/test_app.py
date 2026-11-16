import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.loan_repayments import LoanPaymentAllocation
from app.models import (
    Currency,
    User,
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
)

from app.services.accounting import create_draft_entry, post_entry, reverse_entry
from app.services.ai_manager.data_service import build_financial_snapshot
from app.services.ai_manager.rules_engine import generate_insights
from app.services.ai_manager.providers.groq import sanitize_financial_context
from app.services.loan_schedules import build_repayment_schedule
from app.services.loan_repayments import (
    build_payment_allocations,
    confirm_loan_payment,
)
from flask_jwt_extended import create_access_token


class PesaPilotTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "isolated-test-secret-key-32-bytes-minimum",
            "JWT_SECRET_KEY": "isolated-test-jwt-secret-key-32-bytes-minimum",
            "SQLALCHEMY_DATABASE_URI": "sqlite+pysqlite:///:memory:",
            "SQLALCHEMY_ENGINE_OPTIONS": {},
        })

        cls.app_context = cls.app.app_context()
        cls.app_context.push()

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

        db.session.add_all([
            self.source_ledger,
            self.equity_ledger,
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

    def test_list_budgets(self):
        first = self.create_category("Food")
        second = self.create_category("Transport")

        self.assertEqual(
            self.create_budget(first.json["category"]["id"]).status_code,
            201,
        )
        self.assertEqual(
            self.create_budget(
                second.json["category"]["id"],
                amount=80000,
            ).status_code,
            201,
        )

        response = self.client.get(
            f"/api/v1/budgets?workspace_id={self.workspace.id}",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json["budgets"]), 2)

        names = {
            item["category"]["name"]
            for item in response.json["budgets"]
        }
        self.assertEqual(names, {"Food", "Transport"})

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
        self.assertAlmostEqual(total_interest, 14400, delta=12)
        self.assertGreater(
            max(item["interest_minor"] for item in schedule),
            min(item["interest_minor"] for item in schedule),
        )

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
