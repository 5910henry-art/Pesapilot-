import unittest
from uuid import UUID, uuid4
from datetime import datetime, timezone

from app import create_app
from app.extensions import db
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

)
from app.services.accounting import create_draft_entry, post_entry, reverse_entry
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



if __name__ == "__main__":
    unittest.main(verbosity=2)
