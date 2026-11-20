BEGIN;

-- Running upgrade 9e59aa97e65e -> ef879a53b269

ALTER TABLE financial_accounts ADD CONSTRAINT uq_financial_account_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE ledger_accounts ADD CONSTRAINT uq_ledger_account_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE ledger_accounts ADD CONSTRAINT fk_ledger_financial_account_workspace FOREIGN KEY(workspace_id, financial_account_id) REFERENCES financial_accounts (workspace_id, id);

ALTER TABLE expense_categories ADD CONSTRAINT uq_expense_category_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE expense_categories ADD CONSTRAINT fk_expense_category_ledger_workspace FOREIGN KEY(workspace_id, ledger_account_id) REFERENCES ledger_accounts (workspace_id, id) ON DELETE RESTRICT;

ALTER TABLE budgets ADD CONSTRAINT fk_budget_category_workspace FOREIGN KEY(workspace_id, category_id) REFERENCES expense_categories (workspace_id, id) ON DELETE RESTRICT;

ALTER TABLE savings_goals ADD CONSTRAINT fk_savings_goal_account_workspace FOREIGN KEY(workspace_id, financial_account_id) REFERENCES financial_accounts (workspace_id, id) ON DELETE RESTRICT;

UPDATE alembic_version SET version_num='ef879a53b269' WHERE alembic_version.version_num = '9e59aa97e65e';

COMMIT;

