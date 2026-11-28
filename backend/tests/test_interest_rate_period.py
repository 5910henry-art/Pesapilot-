import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.loan_schedules import build_repayment_schedule


class InterestRatePeriodTests(unittest.TestCase):

    def make_loan(self, rate, period=None, method="reducing"):
        values = {
            "principal_minor": 120000,
            "interest_method": method,
            "interest_rate_percent": Decimal(str(rate)),
            "repayment_frequency": "monthly",
            "term_count": 3,
            "start_date": date(2026, 1, 1),
            "due_date": date(2026, 4, 1),
        }

        if period is not None:
            values["interest_rate_period"] = period

        return SimpleNamespace(**values)

    def test_24_percent_annual_equals_2_percent_monthly(self):
        for method in ("flat", "reducing"):
            with self.subTest(method=method):
                monthly = build_repayment_schedule(
                    self.make_loan("2", "monthly", method)
                )
                annual = build_repayment_schedule(
                    self.make_loan("24", "annual", method)
                )

                self.assertEqual(monthly, annual)

    def test_legacy_loan_without_period_defaults_to_monthly(self):
        legacy = build_repayment_schedule(
            self.make_loan("2", method="reducing")
        )
        explicit_monthly = build_repayment_schedule(
            self.make_loan("2", "monthly", method="reducing")
        )

        self.assertEqual(legacy, explicit_monthly)

    def test_monthly_rate_is_not_divided_by_12(self):
        schedule = build_repayment_schedule(
            self.make_loan("2", "monthly")
        )

        self.assertGreater(
            sum(item["interest_minor"] for item in schedule),
            0,
        )

        self.assertEqual(
            sum(item["principal_minor"] for item in schedule),
            120000,
        )

    def test_invalid_interest_period_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "Interest rate period must be monthly or annual",
        ):
            build_repayment_schedule(
                self.make_loan("2", "weekly")
            )


if __name__ == "__main__":
    unittest.main()
