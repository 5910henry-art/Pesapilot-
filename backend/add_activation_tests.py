from pathlib import Path

path = Path("tests/test_app.py")
source = path.read_text()
marker = "    def make_schedule_loan(self, **overrides):"

tests = '''    def test_activate_personal_loan_generates_schedule(self):
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
        self.assertEqual(activated.json["count"], 12)
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
        self.assertEqual(second.status_code, 400, second.json)
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

'''

if source.count(marker) != 1:
    raise SystemExit("Insertion point check failed; no changes made.")
if "def test_activate_personal_loan_generates_schedule" in source:
    raise SystemExit("Activation tests already exist; no changes made.")

path.write_text(source.replace(marker, tests + marker, 1))
print("Inserted six activation tests.")
