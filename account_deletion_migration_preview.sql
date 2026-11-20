BEGIN;

-- Running upgrade b7e4c2a91f60 -> ebd56bbde521

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_workspace_id_fkey;

ALTER TABLE audit_logs DROP CONSTRAINT audit_logs_user_id_fkey;

ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_user_id_set_null_fkey FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_workspace_id_set_null_fkey FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE SET NULL;

ALTER TABLE bill_payments ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE bill_payments DROP CONSTRAINT bill_payments_created_by_fkey;

ALTER TABLE bill_payments ADD CONSTRAINT bill_payments_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE bills ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE bills DROP CONSTRAINT bills_created_by_fkey;

ALTER TABLE bills ADD CONSTRAINT bills_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE budgets ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE budgets DROP CONSTRAINT budgets_created_by_fkey;

ALTER TABLE budgets ADD CONSTRAINT budgets_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE journal_entries DROP CONSTRAINT journal_entries_created_by_fkey;

ALTER TABLE journal_entries ADD CONSTRAINT journal_entries_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE loan_disbursements ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE loan_disbursements DROP CONSTRAINT fk_disbursement_workspace_recorder;

ALTER TABLE loan_disbursements ADD CONSTRAINT loan_disbursements_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE loan_payments ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE loan_payments DROP CONSTRAINT fk_loan_payment_workspace_recorder;

ALTER TABLE loan_payments ADD CONSTRAINT loan_payments_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE personal_loans ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE personal_loans DROP CONSTRAINT fk_personal_loan_workspace_creator;

ALTER TABLE personal_loans ADD CONSTRAINT personal_loans_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_investor_contributions ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE platform_investor_contributions DROP CONSTRAINT platform_investor_contributions_recorded_by_fkey;

ALTER TABLE platform_investor_contributions ADD CONSTRAINT platform_investor_contributions_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_investor_withdrawals ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE platform_investor_withdrawals DROP CONSTRAINT platform_investor_withdrawals_recorded_by_fkey;

ALTER TABLE platform_investor_withdrawals ADD CONSTRAINT platform_investor_withdrawals_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_investors ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE platform_investors DROP CONSTRAINT platform_investors_created_by_fkey;

ALTER TABLE platform_investors ADD CONSTRAINT platform_investors_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_journal_entries ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE platform_journal_entries DROP CONSTRAINT platform_journal_entries_created_by_fkey;

ALTER TABLE platform_journal_entries DROP CONSTRAINT platform_journal_entries_posted_by_fkey;

ALTER TABLE platform_journal_entries ADD CONSTRAINT platform_journal_entries_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_journal_entries ADD CONSTRAINT platform_journal_entries_posted_by_set_null_fkey FOREIGN KEY(posted_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN full_name DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN national_id_encrypted DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN phone DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN residential_address DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN employment_status DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN monthly_income_minor DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN monthly_expenses_minor DROP NOT NULL;

ALTER TABLE platform_loan_application_details ALTER COLUMN existing_monthly_repayments_minor DROP NOT NULL;

ALTER TABLE platform_loan_application_details DROP CONSTRAINT platform_loan_application_details_customer_id_fkey;

ALTER TABLE platform_loan_application_details ADD CONSTRAINT platform_loan_application_details_customer_id_set_null_fkey FOREIGN KEY(customer_id) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_applications ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE platform_loan_applications ALTER COLUMN purpose DROP NOT NULL;

ALTER TABLE platform_loan_applications DROP CONSTRAINT platform_loan_applications_customer_id_fkey;

ALTER TABLE platform_loan_applications ADD CONSTRAINT platform_loan_applications_customer_id_set_null_fkey FOREIGN KEY(customer_id) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_disbursements ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE platform_loan_disbursements DROP CONSTRAINT platform_loan_disbursements_recorded_by_fkey;

ALTER TABLE platform_loan_disbursements ADD CONSTRAINT platform_loan_disbursements_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_payments ALTER COLUMN recorded_by DROP NOT NULL;

ALTER TABLE platform_loan_payments DROP CONSTRAINT platform_loan_payments_confirmed_by_fkey;

ALTER TABLE platform_loan_payments DROP CONSTRAINT fk_platform_payment_rejected_by;

ALTER TABLE platform_loan_payments DROP CONSTRAINT platform_loan_payments_recorded_by_fkey;

ALTER TABLE platform_loan_payments ADD CONSTRAINT platform_loan_payments_recorded_by_set_null_fkey FOREIGN KEY(recorded_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_payments ADD CONSTRAINT platform_loan_payments_rejected_by_set_null_fkey FOREIGN KEY(rejected_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_payments ADD CONSTRAINT platform_loan_payments_confirmed_by_set_null_fkey FOREIGN KEY(confirmed_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE platform_loan_payments DROP CONSTRAINT ck_platform_payment_rejection;

ALTER TABLE platform_loan_payments ADD CONSTRAINT ck_platform_payment_rejection CHECK (status != 'rejected' OR (rejected_at IS NOT NULL AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0));

ALTER TABLE platform_loan_payments DROP CONSTRAINT ck_platform_payment_confirmation;

ALTER TABLE platform_loan_payments ADD CONSTRAINT ck_platform_payment_confirmation CHECK (status NOT IN ('confirmed', 'reversed') OR (financial_account_id IS NOT NULL AND journal_entry_id IS NOT NULL AND confirmed_at IS NOT NULL AND principal_minor + interest_minor = amount_minor));

ALTER TABLE platform_loans ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE platform_loans DROP CONSTRAINT platform_loans_customer_id_fkey;

ALTER TABLE platform_loans ADD CONSTRAINT platform_loans_customer_id_set_null_fkey FOREIGN KEY(customer_id) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE savings_goals ALTER COLUMN created_by DROP NOT NULL;

ALTER TABLE savings_goals DROP CONSTRAINT savings_goals_created_by_fkey;

ALTER TABLE savings_goals ADD CONSTRAINT savings_goals_created_by_set_null_fkey FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

UPDATE alembic_version SET version_num='ebd56bbde521' WHERE alembic_version.version_num = 'b7e4c2a91f60';

COMMIT;

