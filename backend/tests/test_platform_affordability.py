import unittest

from app.services.platform_affordability import (
    calculate_affordability_capacity,
    classify_affordability,
)


class PlatformAffordabilityTests(unittest.TestCase):

    def test_uses_stricter_of_debt_service_and_disposable_income(self):
        result = calculate_affordability_capacity(
            monthly_income_minor=4_000_000,
            monthly_expenses_minor=2_000_000,
            existing_monthly_repayments_minor=500_000,
        )
        self.assertEqual(
            result["maximum_total_monthly_debt_service_minor"], 1_400_000
        )
        self.assertEqual(result["disposable_income_minor"], 1_500_000)
        self.assertEqual(
            result["recommended_monthly_instalment_minor"], 900_000
        )

    def test_returns_zero_when_existing_debt_exceeds_ceiling(self):
        result = calculate_affordability_capacity(
            monthly_income_minor=1_000_000,
            monthly_expenses_minor=200_000,
            existing_monthly_repayments_minor=500_000,
        )
        self.assertEqual(
            result["recommended_monthly_instalment_minor"], 0
        )

    def test_returns_zero_when_expenses_exceed_income(self):
        result = calculate_affordability_capacity(
            monthly_income_minor=1_000_000,
            monthly_expenses_minor=1_100_000,
            existing_monthly_repayments_minor=0,
        )
        self.assertEqual(
            result["recommended_monthly_instalment_minor"], 0
        )

    def test_rejects_negative_amounts(self):
        values = {
            "monthly_income_minor": 1_000_000,
            "monthly_expenses_minor": 200_000,
            "existing_monthly_repayments_minor": 0,
        }
        for field in values:
            with self.subTest(field=field):
                invalid_values = values.copy()
                invalid_values[field] = -1
                with self.assertRaises(ValueError):
                    calculate_affordability_capacity(**invalid_values)

    def test_serious_repayment_concerns_take_priority(self):
        self.assertEqual(
            classify_affordability(
                has_sufficient_records=True,
                has_material_discrepancy=True,
                has_serious_repayment_concerns=True,
            ),
            "high_risk",
        )

    def test_discrepancies_require_review(self):
        self.assertEqual(
            classify_affordability(
                has_sufficient_records=True,
                has_material_discrepancy=True,
            ),
            "needs_review",
        )

    def test_missing_records_do_not_automatically_reject(self):
        self.assertEqual(
            classify_affordability(has_sufficient_records=False),
            "insufficient_records",
        )


if __name__ == "__main__":
    unittest.main()


class AffordablePrincipalScheduleTests(unittest.TestCase):

    def estimate(self, frequency, term, due_date):
        from datetime import date
        from app.services.platform_affordability import (
            estimate_affordable_principal,
        )

        return estimate_affordable_principal(
            monthly_capacity_minor=100_000,
            maximum_principal_minor=2_000_000,
            term_count=term,
            start_date=date(2026, 1, 1),
            due_date=due_date,
            repayment_frequency=frequency,
            interest_method="none",
            interest_rate_percent=0,
        )

    def test_monthly_schedule_fits_monthly_capacity(self):
        result = self.estimate("monthly", 6, __import__("datetime").date(2026, 7, 1))
        self.assertGreater(result["estimated_affordable_principal_minor"], 0)
        self.assertEqual(len(result["schedule"]), 6)

    def test_quarterly_schedule_accumulates_capacity(self):
        result = self.estimate("quarterly", 2, __import__("datetime").date(2026, 7, 1))
        self.assertGreater(result["estimated_affordable_principal_minor"], 0)
        self.assertEqual(len(result["schedule"]), 2)

    def test_one_time_schedule_uses_actual_due_date(self):
        result = self.estimate("one_time", 1, __import__("datetime").date(2026, 4, 1))
        self.assertGreater(result["estimated_affordable_principal_minor"], 0)
        self.assertEqual(len(result["schedule"]), 1)

    def test_one_time_rejects_multiple_installments(self):
        from datetime import date
        from app.services.platform_affordability import (
            estimate_affordable_principal,
        )

        with self.assertRaises(ValueError):
            estimate_affordable_principal(
                monthly_capacity_minor=100_000,
                maximum_principal_minor=500_000,
                term_count=2,
                start_date=date(2026, 1, 1),
                due_date=date(2026, 3, 1),
                repayment_frequency="one_time",
                interest_method="none",
                interest_rate_percent=0,
            )


class ExistingDebtFrequencyTests(unittest.TestCase):
    def test_weekly_payment_converts_to_monthly_equivalent(self):
        from app.services.platform_affordability_evidence import _monthly_equivalent
        self.assertEqual(_monthly_equivalent(1000, "weekly"), 4333)

    def test_biweekly_payment_converts_to_monthly_equivalent(self):
        from app.services.platform_affordability_evidence import _monthly_equivalent
        self.assertEqual(_monthly_equivalent(1000, "biweekly"), 2166)

    def test_monthly_payment_remains_unchanged(self):
        from app.services.platform_affordability_evidence import _monthly_equivalent
        self.assertEqual(_monthly_equivalent(2500, "monthly"), 2500)

    def test_quarterly_payment_converts_to_monthly_equivalent(self):
        from app.services.platform_affordability_evidence import _monthly_equivalent
        self.assertEqual(_monthly_equivalent(3000, "quarterly"), 1000)

    def test_one_time_payment_uses_actual_due_date(self):
        from datetime import date
        from app.services.platform_affordability_evidence import _monthly_equivalent

        self.assertEqual(
            _monthly_equivalent(
                9000,
                "one_time",
                due_date=date(2027, 2, 16),
                as_of=date(2026, 11, 18),
            ),
            3043,
        )

    def test_one_time_payment_without_due_date_returns_zero(self):
        from app.services.platform_affordability_evidence import _monthly_equivalent
        self.assertEqual(_monthly_equivalent(9000, "one_time"), 0)
