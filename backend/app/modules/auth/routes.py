import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from email_validator import EmailNotValidError, validate_email
from flask import current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import (
    BusinessProfile,
    User,
    UserProfile,
    Workspace,
    WorkspaceMember,
    WorkspaceInvitation,
    Currency,
    LedgerAccount,
    AuditLog,
    RefreshToken,
    PlatformAdmin,
    PlatformLoan,
    PlatformLoanApplication,
    PlatformLoanPayment,
    PlatformLoanDisbursement,
    PlatformLoanApplicationDetails,
    PlatformLoanAffordabilityAssessment,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    ExpenseCategory,
    Budget,
    SavingsGoal,
    SavingsGoalMovement,
    PersonalLoan,
    LoanInstallment,
    LoanPayment,
    LoanPaymentAllocation,
    LoanDisbursement,
    Bill,
    BillPayment,
)

from . import auth_bp


DEFAULT_LEDGER_ACCOUNTS = [
    ("1000", "Cash on Hand", "asset", "debit"),
    ("1010", "Bank Accounts", "asset", "debit"),
    ("1020", "Mobile Money", "asset", "debit"),
    ("1100", "Savings", "asset", "debit"),
    ("1200", "Accounts Receivable", "asset", "debit"),
    ("2000", "Loans and Credit Payable", "liability", "credit"),
    ("2100", "Accounts Payable", "liability", "credit"),
    ("3000", "Owner's Equity", "equity", "credit"),
    ("4000", "Income", "income", "credit"),
    ("5000", "Expenses", "expense", "debit"),
]


@auth_bp.get("/health")
def auth_health():
    return {
        "success": True,
        "module": "authentication",
        "status": "ready",
    }, 200


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    first_name = data.get("first_name")
    last_name = data.get("last_name")
    email = data.get("email")
    password = data.get("password")
    phone = data.get("phone")
    currency_code = data.get("currency", "KES")

    if not all(
        isinstance(value, str)
        for value in (first_name, last_name, email, password, currency_code)
    ):
        return jsonify({
            "success": False,
            "message": "Name, email, password and currency fields must be strings.",
        }), 400

    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()
    currency_code = currency_code.strip().upper()

    if not first_name or len(first_name) > 100:
        return jsonify({
            "success": False,
            "message": "First name is required and must not exceed 100 characters.",
        }), 400

    if not last_name or len(last_name) > 100:
        return jsonify({
            "success": False,
            "message": "Last name is required and must not exceed 100 characters.",
        }), 400

    if not email or len(email) > 255:
        return jsonify({
            "success": False,
            "message": "A valid email address is required.",
        }), 400

    try:
        email = validate_email(
            email,
            check_deliverability=False,
        ).normalized
    except EmailNotValidError:
        return jsonify({
            "success": False,
            "message": "Please provide a valid email address.",
        }), 400

    if len(password) < 12 or len(password) > 128:
        return jsonify({
            "success": False,
            "message": "Password must be between 12 and 128 characters.",
        }), 400

    if len(currency_code) != 3 or not currency_code.isalpha():
        return jsonify({
            "success": False,
            "message": "Please provide a valid three-letter currency code.",
        }), 400

    if phone is not None:
        if not isinstance(phone, str):
            return jsonify({
                "success": False,
                "message": "Phone number must be a string.",
            }), 400

        phone = phone.strip()

        if phone:
            if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
                return jsonify({
                    "success": False,
                    "message": "Phone number must use international format, for example +254712345678.",
                }), 400
        else:
            phone = None

    currency = db.session.get(Currency, currency_code)

    if currency is None or not currency.is_active:
        return jsonify({
            "success": False,
            "message": "The selected currency is unavailable.",
        }), 400

    existing_user = db.session.query(User).filter(
        or_(
            User.email == email,
            User.phone == phone if phone else False,
        )
    ).first()

    if existing_user:
        return jsonify({
            "success": False,
            "message": "An account with those details already exists.",
        }), 409

    try:
        user = User(
            email=email,
            phone=phone,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.flush()

        profile = UserProfile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            timezone="Africa/Nairobi",
            preferred_currency=currency_code,
        )

        workspace = Workspace(
            name=f"{first_name}'s Personal Workspace",
            workspace_type="personal",
            functional_currency=currency_code,
        )
        db.session.add_all([profile, workspace])
        db.session.flush()

        membership = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
        )

        ledger_accounts = [
            LedgerAccount(
                workspace_id=workspace.id,
                code=code,
                name=name,
                account_class=account_class,
                normal_balance=normal_balance,
                currency_code=currency_code,
                is_system=True,
                is_active=True,
            )
            for code, name, account_class, normal_balance
            in DEFAULT_LEDGER_ACCOUNTS
        ]

        audit = AuditLog(
            workspace_id=workspace.id,
            user_id=user.id,
            action="user_registered",
            resource_type="user",
            resource_id=str(user.id),
            details={"currency": currency_code},
            ip_address=request.remote_addr,
        )

        db.session.add_all([
            membership,
            *ledger_accounts,
            audit,
        ])

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Your PesaPilot account has been created.",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "first_name": first_name,
                "last_name": last_name,
            },
            "workspace": {
                "id": str(workspace.id),
                "name": workspace.name,
                "currency": currency_code,
                "role": "owner",
            },
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "An account with those details already exists.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Registration failed")
        return jsonify({
            "success": False,
            "message": "Registration could not be completed. Please try again.",
        }), 500


# ---------------------------------------------------------
# TOKEN HELPERS
# ---------------------------------------------------------

def hash_refresh_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_bearer_token():
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    return parts[1]


# ---------------------------------------------------------
# LOGIN
# ---------------------------------------------------------

@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({
            "success": False,
            "message": "Email and password are required.",
        }), 400

    email = email.strip().lower()

    user = db.session.query(User).filter_by(email=email).first()

    if (
        user is None
        or not check_password_hash(user.password_hash, password)
    ):
        return jsonify({
            "success": False,
            "message": "Invalid email or password.",
        }), 401

    if not user.is_active:
        return jsonify({
            "success": False,
            "message": "This account is inactive.",
        }), 403

    try:
        session_id = uuid4()
        session_claims = {"sid": str(session_id)}

        access_token = create_access_token(
            identity=str(user.id),
            additional_claims=session_claims,
        )

        refresh_token = create_refresh_token(
            identity=str(user.id),
            additional_claims=session_claims,
        )

        token_record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            session_id=session_id,
            user_agent=request.headers.get("User-Agent", "")[:512] or None,
            ip_address=request.remote_addr,
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(
                    seconds=current_app.config[
                        "JWT_REFRESH_TOKEN_EXPIRES"
                    ]
                )
            ),
        )

        db.session.add(token_record)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Login successful.",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "id": str(user.id),
                "email": user.email,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Login failed")

        return jsonify({
            "success": False,
            "message": "Login could not be completed.",
        }), 500


# ---------------------------------------------------------
# REFRESH TOKEN ROTATION
# ---------------------------------------------------------

@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    raw_token = get_bearer_token()

    if not raw_token:
        return jsonify({
            "success": False,
            "message": "Refresh token is required.",
        }), 401

    try:
        user_id = UUID(get_jwt_identity())

        old_record = db.session.query(RefreshToken).filter_by(
            token_hash=hash_refresh_token(raw_token)
        ).with_for_update().first()

        now = datetime.now(timezone.utc)

        if (
            old_record is None
            or old_record.user_id != user_id
            or old_record.revoked_at is not None
            or old_record.expires_at <= now
        ):
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Refresh token is invalid or revoked.",
            }), 401

        user = db.session.get(User, user_id)

        if user is None or not user.is_active:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Account is unavailable.",
            }), 403

        session_claims = {"sid": str(old_record.session_id)}

        new_refresh_token = create_refresh_token(
            identity=str(user.id),
            additional_claims=session_claims,
        )

        new_record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(new_refresh_token),
            session_id=old_record.session_id,
            user_agent=old_record.user_agent,
            ip_address=old_record.ip_address,
            expires_at=(
                now + timedelta(
                    seconds=current_app.config[
                        "JWT_REFRESH_TOKEN_EXPIRES"
                    ]
                )
            ),
        )

        old_record.revoked_at = now
        db.session.add(new_record)

        new_access_token = create_access_token(
            identity=str(user.id),
            additional_claims=session_claims,
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Token refresh failed")

        return jsonify({
            "success": False,
            "message": "Token refresh could not be completed.",
        }), 500


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

@auth_bp.post("/logout")
@jwt_required(refresh=True)
def logout():
    raw_token = get_bearer_token()

    if not raw_token:
        return jsonify({
            "success": False,
            "message": "Refresh token is required.",
        }), 401

    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        record = db.session.query(RefreshToken).filter_by(
            token_hash=hash_refresh_token(raw_token)
        ).with_for_update().first()

        if record is None:
            return jsonify({
                "success": False,
                "message": "Refresh token is invalid.",
            }), 401

        if record.user_id != user_id:
            return jsonify({
                "success": False,
                "message": "Refresh token does not belong to this user.",
            }), 403

        now = datetime.now(timezone.utc)
        expires_at = record.expires_at

        # SQLite may return timezone-naive UTC datetimes.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if record.revoked_at is not None or expires_at <= now:
            return jsonify({
                "success": False,
                "message": "Refresh token is expired or revoked.",
            }), 401

        revoked_count = db.session.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id == record.session_id,
            RefreshToken.revoked_at.is_(None),
        ).update(
            {RefreshToken.revoked_at: now},
            synchronize_session=False,
        )

        db.session.add(AuditLog(
            workspace_id=None,
            user_id=user_id,
            action="user_logged_out",
            resource_type="auth_session",
            resource_id=str(record.session_id),
            details={"revoked_token_count": revoked_count},
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Logout successful.",
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Logout failed")

        return jsonify({
            "success": False,
            "message": "Logout could not be completed.",
        }), 500


# ---------------------------------------------------------
# ACTIVE SESSIONS
# ---------------------------------------------------------

@auth_bp.get("/sessions")
@jwt_required()
def list_active_sessions():
    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    claims = get_jwt()
    current_session_id = None

    try:
        if claims.get("sid"):
            current_session_id = UUID(str(claims["sid"]))
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid session identity.",
        }), 401

    now = datetime.now(timezone.utc)

    records = db.session.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.expires_at > now,
    ).order_by(RefreshToken.created_at.asc()).all()

    grouped = {}
    for record in records:
        grouped.setdefault(record.session_id, []).append(record)

    sessions = []

    for session_id, session_records in grouped.items():
        active_records = [
            item for item in session_records
            if item.revoked_at is None
        ]

        if not active_records:
            continue

        latest = max(
            session_records,
            key=lambda item: item.created_at,
        )

        first_seen = min(
            item.created_at for item in session_records
        )
        last_seen = max(
            item.created_at for item in session_records
        )

        sessions.append({
            "session_id": str(session_id),
            "created_at": first_seen.isoformat(),
            "last_seen_at": last_seen.isoformat(),
            "user_agent": latest.user_agent,
            "ip_address": latest.ip_address,
            "is_current": session_id == current_session_id,
        })

    sessions.sort(
        key=lambda item: item["last_seen_at"],
        reverse=True,
    )

    return jsonify({
        "success": True,
        "sessions": sessions,
        "count": len(sessions),
    }), 200


@auth_bp.delete("/sessions/<session_id>")
@jwt_required()
def revoke_session(session_id):
    try:
        user_id = UUID(get_jwt_identity())
        target_session_id = UUID(session_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid user or session identity.",
        }), 400

    claims = get_jwt()

    try:
        current_session_id = UUID(str(claims["sid"]))
    except (KeyError, ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Current session cannot be identified.",
        }), 400

    if target_session_id == current_session_id:
        return jsonify({
            "success": False,
            "message": "Use logout to end the current session.",
        }), 400

    try:
        now = datetime.now(timezone.utc)

        revoked_count = db.session.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id == target_session_id,
            RefreshToken.revoked_at.is_(None),
        ).update(
            {RefreshToken.revoked_at: now},
            synchronize_session=False,
        )

        if not revoked_count:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Active session was not found.",
            }), 404

        db.session.add(AuditLog(
            workspace_id=None,
            user_id=user_id,
            action="session_revoked",
            resource_type="auth_session",
            resource_id=str(target_session_id),
            details={"revoked_token_count": revoked_count},
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Session revoked successfully.",
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Session revocation failed")

        return jsonify({
            "success": False,
            "message": "Session could not be revoked.",
        }), 500


@auth_bp.post("/sessions/revoke-others")
@jwt_required()
def revoke_other_sessions():
    try:
        user_id = UUID(get_jwt_identity())
        current_session_id = UUID(str(get_jwt()["sid"]))
    except (KeyError, ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Current session cannot be identified.",
        }), 401

    try:
        now = datetime.now(timezone.utc)

        revoked_count = db.session.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id != current_session_id,
            RefreshToken.revoked_at.is_(None),
        ).update(
            {RefreshToken.revoked_at: now},
            synchronize_session=False,
        )

        db.session.add(AuditLog(
            workspace_id=None,
            user_id=user_id,
            action="other_sessions_revoked",
            resource_type="auth_session",
            resource_id=str(current_session_id),
            details={"revoked_token_count": revoked_count},
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Other sessions have been revoked.",
            "revoked_token_count": revoked_count,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Other session revocation failed")

        return jsonify({
            "success": False,
            "message": "Other sessions could not be revoked.",
        }), 500


@auth_bp.post("/change-password")
@jwt_required()
def change_password():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    current_password = data.get("current_password")
    new_password = data.get("new_password")

    if not isinstance(current_password, str) or not isinstance(new_password, str):
        return jsonify({
            "success": False,
            "message": "Current and new passwords are required.",
        }), 400

    if len(new_password) < 12 or len(new_password) > 128:
        return jsonify({
            "success": False,
            "message": "Password must be between 12 and 128 characters.",
        }), 400

    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        user = db.session.get(User, user_id)

        if user is None or not user.is_active:
            return jsonify({
                "success": False,
                "message": "Account is unavailable.",
            }), 403

        if not check_password_hash(user.password_hash, current_password):
            return jsonify({
                "success": False,
                "message": "Current password is incorrect.",
            }), 400

        if check_password_hash(user.password_hash, new_password):
            return jsonify({
                "success": False,
                "message": "New password must be different from the current password.",
            }), 400

        now = datetime.now(timezone.utc)
        user.password_hash = generate_password_hash(new_password)

        revoked_count = db.session.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        ).update(
            {RefreshToken.revoked_at: now},
            synchronize_session=False,
        )

        db.session.add(AuditLog(
            workspace_id=None,
            user_id=user_id,
            action="password_changed",
            resource_type="user",
            resource_id=str(user_id),
            details={"revoked_session_token_count": revoked_count},
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Password changed successfully. Please sign in again.",
            "revoked_session_token_count": revoked_count,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Password change failed")

        return jsonify({
            "success": False,
            "message": "Password could not be changed.",
        }), 500


# ACCOUNT DELETION ELIGIBILITY
# ---------------------------------------------------------

@auth_bp.get("/account-deletion/eligibility")
@jwt_required()
def account_deletion_eligibility():
    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        user = db.session.get(User, user_id)
        if user is None or not user.is_active:
            return jsonify({
                "success": False,
                "message": "Account is unavailable.",
            }), 403

        blockers = []

        # Outstanding or unfinished platform loans.
        loans = PlatformLoan.query.filter_by(
            customer_id=user_id
        ).all()

        outstanding_count = sum(
            1 for loan in loans
            if loan.outstanding_principal_minor > 0
        )
        unfinished_count = sum(
            1 for loan in loans
            if loan.status in {"created", "funding"}
        )

        if outstanding_count:
            blockers.append({
                "code": "outstanding_platform_loan",
                "message": (
                    "Settle all outstanding PesaPilot platform loans "
                    "before deleting your account."
                ),
                "count": outstanding_count,
            })

        if unfinished_count:
            blockers.append({
                "code": "unfinished_platform_loan",
                "message": (
                    "A platform loan is still being created or funded. "
                    "Resolve it before deleting your account."
                ),
                "count": unfinished_count,
            })

        pending_applications = PlatformLoanApplication.query.filter_by(
            customer_id=user_id,
            status="pending",
        ).count()

        if pending_applications:
            blockers.append({
                "code": "pending_platform_application",
                "message": (
                    "You have a pending PesaPilot loan application."
                ),
                "count": pending_applications,
            })

        pending_payments = (
            db.session.query(PlatformLoanPayment)
            .join(
                PlatformLoan,
                PlatformLoan.id == PlatformLoanPayment.loan_id,
            )
            .filter(
                PlatformLoan.customer_id == user_id,
                PlatformLoanPayment.status == "pending",
            )
            .count()
        )

        if pending_payments:
            blockers.append({
                "code": "pending_platform_payment",
                "message": (
                    "A platform loan repayment is still pending. "
                    "Wait until it is resolved."
                ),
                "count": pending_payments,
            })

        # A shared workspace with other members must retain its owner.
        owned_shared_workspaces = []
        memberships = (
            db.session.query(WorkspaceMember, Workspace)
            .join(
                Workspace,
                Workspace.id == WorkspaceMember.workspace_id,
            )
            .filter(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.role == "owner",
            )
            .all()
        )

        for membership, workspace in memberships:
            other_members = WorkspaceMember.query.filter(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id != user_id,
            ).count()

            if other_members:
                owned_shared_workspaces.append({
                    "workspace_id": str(workspace.id),
                    "workspace_name": workspace.name,
                })

        if owned_shared_workspaces:
            blockers.append({
                "code": "shared_workspace_ownership",
                "message": (
                    "Transfer ownership of each shared workspace "
                    "before deleting your account."
                ),
                "workspaces": owned_shared_workspaces,
            })

        # Do not remove the final active platform administrator.
        admin_access = db.session.get(PlatformAdmin, user_id)
        if admin_access is not None and admin_access.is_active:
            active_admin_count = PlatformAdmin.query.filter_by(
                is_active=True
            ).count()

            if active_admin_count <= 1:
                blockers.append({
                    "code": "last_platform_admin",
                    "message": (
                        "Another active platform administrator must "
                        "be appointed before deleting this account."
                    ),
                })

        return jsonify({
            "success": True,
            "eligible": not blockers,
            "blockers": blockers,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Account deletion eligibility check failed"
        )
        return jsonify({
            "success": False,
            "message": "Account deletion eligibility could not be checked.",
        }), 500



# PERMANENT ACCOUNT DELETION
# ---------------------------------------------------------

@auth_bp.delete("/account-deletion")
@jwt_required()
def permanently_delete_account():
    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    password = data.get("password")
    confirmation = data.get("confirmation")

    if not isinstance(password, str) or not password:
        return jsonify({
            "success": False,
            "message": "Enter your current password to continue.",
        }), 400

    if confirmation != "DELETE":
        return jsonify({
            "success": False,
            "message": 'Type DELETE exactly to confirm permanent deletion.',
        }), 400

    try:
        # Lock the account row where supported and recheck eligibility
        # inside this transaction, rather than trusting an earlier GET.
        user = (
            db.session.query(User)
            .filter(User.id == user_id)
            .with_for_update()
            .first()
        )

        if user is None or not user.is_active:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Account is unavailable.",
            }), 404

        if not check_password_hash(user.password_hash, password):
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "The password is incorrect.",
            }), 401

        # Recheck all financial and workspace blockers.
        blockers = []

        loans = PlatformLoan.query.filter_by(customer_id=user_id).all()

        outstanding_count = sum(
            1 for loan in loans
            if loan.outstanding_principal_minor > 0
        )
        unfinished_count = sum(
            1 for loan in loans
            if loan.status in {"created", "funding"}
        )

        if outstanding_count:
            blockers.append({
                "code": "outstanding_platform_loan",
                "message": "Settle all outstanding PesaPilot platform loans before deleting your account.",
                "count": outstanding_count,
            })

        if unfinished_count:
            blockers.append({
                "code": "unfinished_platform_loan",
                "message": "A platform loan is still being created or funded.",
                "count": unfinished_count,
            })

        pending_applications = PlatformLoanApplication.query.filter_by(
            customer_id=user_id,
            status="pending",
        ).count()

        if pending_applications:
            blockers.append({
                "code": "pending_platform_application",
                "message": "You have a pending PesaPilot loan application.",
                "count": pending_applications,
            })

        pending_payments = (
            db.session.query(PlatformLoanPayment)
            .join(
                PlatformLoan,
                PlatformLoan.id == PlatformLoanPayment.loan_id,
            )
            .filter(
                PlatformLoan.customer_id == user_id,
                PlatformLoanPayment.status == "pending",
            )
            .count()
        )

        if pending_payments:
            blockers.append({
                "code": "pending_platform_payment",
                "message": "A platform loan repayment is still pending.",
                "count": pending_payments,
            })

        memberships = (
            db.session.query(WorkspaceMember, Workspace)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .filter(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.role == "owner",
            )
            .all()
        )

        owned_shared_workspaces = []
        for membership, workspace in memberships:
            other_members = WorkspaceMember.query.filter(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id != user_id,
            ).count()

            if other_members:
                owned_shared_workspaces.append({
                    "workspace_id": str(workspace.id),
                    "workspace_name": workspace.name,
                })

        if owned_shared_workspaces:
            blockers.append({
                "code": "shared_workspace_ownership",
                "message": "Transfer ownership of each shared workspace before deleting your account.",
                "workspaces": owned_shared_workspaces,
            })

        admin_access = db.session.get(PlatformAdmin, user_id)
        if admin_access is not None and admin_access.is_active:
            active_admin_count = PlatformAdmin.query.filter_by(
                is_active=True
            ).count()
            if active_admin_count <= 1:
                blockers.append({
                    "code": "last_platform_admin",
                    "message": "Another active platform administrator must be appointed first.",
                })

        if blockers:
            db.session.rollback()
            return jsonify({
                "success": False,
                "eligible": False,
                "blockers": blockers,
                "message": "Resolve the listed requirements before deleting your account.",
            }), 409

        # Workspaces with no other members are owned solely by this user
        # and can be removed. Shared workspaces are preserved.
        user_memberships = WorkspaceMember.query.filter_by(
            user_id=user_id
        ).all()

        owned_workspace_ids = []
        shared_workspace_ids = []

        for membership in user_memberships:
            other_count = WorkspaceMember.query.filter(
                WorkspaceMember.workspace_id == membership.workspace_id,
                WorkspaceMember.user_id != user_id,
            ).count()

            workspace = db.session.get(Workspace, membership.workspace_id)

            is_sole_member = other_count == 0
            is_deletable_personal = (
                workspace is not None
                and workspace.workspace_type == "personal"
            )
            is_sole_business_owner = membership.role == "owner"

            if is_sole_member and (
                is_deletable_personal or is_sole_business_owner
            ):
                owned_workspace_ids.append(membership.workspace_id)
            else:
                # Preserve shared workspaces and business workspaces the
                # departing user does not solely own.
                shared_workspace_ids.append(membership.workspace_id)

        # Anonymise historical platform loan records before deleting any
        # personal workspace, account, or journal entry they reference.
        application_ids = [
            row[0]
            for row in db.session.query(PlatformLoanApplication.id).filter(
                PlatformLoanApplication.customer_id == user_id
            ).all()
        ]
        platform_loan_ids = [
            row[0]
            for row in db.session.query(PlatformLoan.id).filter(
                PlatformLoan.customer_id == user_id
            ).all()
        ]

        if application_ids:
            details_rows = PlatformLoanApplicationDetails.query.filter(
                PlatformLoanApplicationDetails.application_id.in_(application_ids)
            ).all()

            for details in details_rows:
                details.customer_id = None
                details.customer_financial_account_id = None
                details.full_name = None
                details.national_id_encrypted = None
                details.phone = None
                details.email = None
                details.residential_address = None
                details.employment_status = None
                details.employer_business_name = None
                details.monthly_income_minor = None
                details.monthly_expenses_minor = None
                details.existing_monthly_repayments_minor = None
                details.explanation = None
                details.alternative_contact_name = None
                details.alternative_contact_phone = None
                details.alternative_contact_relationship = None
                details.additional_notes = None
                details.affordability_consent = False
                details.affordability_workspace_id = None
                details.affordability_consent_at = None

            assessments = PlatformLoanAffordabilityAssessment.query.filter(
                PlatformLoanAffordabilityAssessment.application_id.in_(application_ids)
            ).all()

            for assessment in assessments:
                assessment.source_workspace_id = None
                assessment.report = {
                    "redacted": True,
                    "reason": "account_deletion",
                }
                assessment.reasons = []

            applications = PlatformLoanApplication.query.filter(
                PlatformLoanApplication.id.in_(application_ids)
            ).all()

            for application in applications:
                application.customer_id = None
                application.purpose = None
                application.decision_notes = None

        PlatformLoan.query.filter_by(customer_id=user_id).update(
            {PlatformLoan.customer_id: None},
            synchronize_session=False,
        )

        # Retain platform financial history, but remove personal-workspace
        # references and free-text notes that may identify the borrower.
        if platform_loan_ids:
            PlatformLoanDisbursement.query.filter(
                PlatformLoanDisbursement.loan_id.in_(platform_loan_ids)
            ).update(
                {PlatformLoanDisbursement.notes: None},
                synchronize_session=False,
            )
            PlatformLoanPayment.query.filter(
                PlatformLoanPayment.loan_id.in_(platform_loan_ids),
                PlatformLoanPayment.status == "rejected",
            ).update(
                {
                    PlatformLoanPayment.notes: None,
                    PlatformLoanPayment.rejection_reason:
                        "Redacted after account deletion",
                },
                synchronize_session=False,
            )

            PlatformLoanPayment.query.filter(
                PlatformLoanPayment.loan_id.in_(platform_loan_ids),
                PlatformLoanPayment.status != "rejected",
            ).update(
                {
                    PlatformLoanPayment.notes: None,
                    PlatformLoanPayment.rejection_reason: None,
                },
                synchronize_session=False,
            )

        if owned_workspace_ids:
            PlatformLoanDisbursement.query.filter(
                PlatformLoanDisbursement.customer_workspace_id.in_(
                    owned_workspace_ids
                )
            ).update(
                {
                    PlatformLoanDisbursement.customer_workspace_id: None,
                    PlatformLoanDisbursement.customer_financial_account_id: None,
                    PlatformLoanDisbursement.customer_journal_entry_id: None,
                },
                synchronize_session=False,
            )

            PlatformLoanPayment.query.filter(
                PlatformLoanPayment.borrower_financial_account_id.in_(
                    db.session.query(FinancialAccount.id).filter(
                        FinancialAccount.workspace_id.in_(owned_workspace_ids)
                    )
                )
            ).update(
                {PlatformLoanPayment.borrower_financial_account_id: None},
                synchronize_session=False,
            )

        # Retain audit events, but remove personal actor, IP, resource and
        # arbitrary JSON details. Shared-workspace records remain present.
        AuditLog.query.filter(
            AuditLog.user_id == user_id
        ).update(
            {
                AuditLog.user_id: None,
                AuditLog.ip_address: None,
                AuditLog.resource_id: None,
                AuditLog.details: {"redacted": "account_deletion"},
            },
            synchronize_session=False,
        )

        if owned_workspace_ids:
            AuditLog.query.filter(
                AuditLog.workspace_id.in_(owned_workspace_ids)
            ).update(
                {
                    AuditLog.workspace_id: None,
                    AuditLog.ip_address: None,
                    AuditLog.resource_id: None,
                    AuditLog.details: {"redacted": "workspace_deletion"},
                },
                synchronize_session=False,
            )

            # Remove personal-workspace records in dependency order.
            SavingsGoalMovement.query.filter(
                SavingsGoalMovement.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            LoanPaymentAllocation.query.filter(
                LoanPaymentAllocation.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            LoanPayment.query.filter(
                LoanPayment.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            LoanDisbursement.query.filter(
                LoanDisbursement.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            LoanInstallment.query.filter(
                LoanInstallment.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            # Anonymise this user as counterparty on loans in retained workspaces.
            PersonalLoan.query.filter(
                PersonalLoan.counterparty_user_id == user_id
            ).update(
                {
                    PersonalLoan.counterparty_user_id: None,
                    PersonalLoan.counterparty_name: "Deleted user",
                    PersonalLoan.counterparty_phone: None,
                    PersonalLoan.description: None,
                },
                synchronize_session=False,
            )

            PersonalLoan.query.filter(
                PersonalLoan.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            BillPayment.query.filter(
                BillPayment.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            Bill.query.filter(
                Bill.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            Budget.query.filter(
                Budget.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            SavingsGoal.query.filter(
                SavingsGoal.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            journal_ids = select(JournalEntry.id).where(
                JournalEntry.workspace_id.in_(owned_workspace_ids)
            )

            # Clear reversal links both within and into the deleted journals.
            JournalEntry.query.filter(
                or_(
                    JournalEntry.workspace_id.in_(owned_workspace_ids),
                    JournalEntry.reversal_of_id.in_(journal_ids),
                )
            ).update(
                {JournalEntry.reversal_of_id: None},
                synchronize_session=False,
            )

            JournalLine.query.filter(
                JournalLine.journal_entry_id.in_(journal_ids)
            ).delete(synchronize_session=False)

            JournalEntry.query.filter(
                JournalEntry.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            ExpenseCategory.query.filter(
                ExpenseCategory.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            # Journal lines and expense categories are gone, so ledger
            # accounts can now be removed before their financial accounts.
            LedgerAccount.query.filter(
                LedgerAccount.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            FinancialAccount.query.filter(
                FinancialAccount.workspace_id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

            Workspace.query.filter(
                Workspace.id.in_(owned_workspace_ids)
            ).delete(synchronize_session=False)

        # Explicitly remove the user's membership from surviving shared
        # workspaces. Other members and all shared data remain untouched.
        WorkspaceMember.query.filter_by(user_id=user_id).delete(
            synchronize_session=False
        )

        # Delete the account last. Refresh tokens, profile and platform
        # administrator membership have user-level CASCADE constraints.
        db.session.delete(user)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Your PesaPilot account has been permanently deleted.",
            "deleted_workspace_count": len(owned_workspace_ids),
            "preserved_shared_workspace_count": len(shared_workspace_ids),
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Permanent account deletion failed")
        return jsonify({
            "success": False,
            "message": "Account deletion failed. No changes were committed.",
        }), 500


# CURRENT AUTHENTICATED USER
# ---------------------------------------------------------

@auth_bp.get("/me")
@jwt_required()
def get_current_user():
    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    user = db.session.get(User, user_id)

    if user is None or not user.is_active:
        return jsonify({
            "success": False,
            "message": "Account is unavailable.",
        }), 403

    profile = db.session.get(UserProfile, user_id)

    memberships = (
        db.session.query(WorkspaceMember, Workspace)
        .join(
            Workspace,
            Workspace.id == WorkspaceMember.workspace_id,
        )
        .filter(WorkspaceMember.user_id == user_id)
        .all()
    )

    return jsonify({
        "success": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
            "is_active": user.is_active,
            "email_verified": user.email_verified,
            "phone_verified": user.phone_verified,
            "created_at": user.created_at.isoformat(),
        },
        "profile": {
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "timezone": profile.timezone,
            "preferred_currency": profile.preferred_currency,
        } if profile else None,
        "workspaces": [
            {
                "id": str(workspace.id),
                "name": workspace.name,
                "type": workspace.workspace_type,
                "currency": workspace.functional_currency,
                "role": membership.role,
                "joined_at": membership.joined_at.isoformat(),
            }
            for membership, workspace in memberships
        ],
    }), 200



@auth_bp.get("/currencies")
@jwt_required()
def list_active_currencies():
    currencies = (
        Currency.query
        .filter(Currency.is_active.is_(True))
        .order_by(Currency.code.asc())
        .all()
    )

    return jsonify({
        "success": True,
        "currencies": [
            {
                "code": currency.code,
                "name": currency.name,
            }
            for currency in currencies
        ],
    }), 200


# UPDATE PERSONAL PROFILE
# ---------------------------------------------------------

@auth_bp.patch("/profile")
@jwt_required()
def update_current_user_profile():
    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    user = db.session.get(User, user_id)

    if user is None or not user.is_active:
        return jsonify({
            "success": False,
            "message": "Account is unavailable.",
        }), 403

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    allowed_fields = {
        "first_name",
        "last_name",
        "phone",
        "preferred_currency",
    }

    unknown_fields = set(data) - allowed_fields

    if unknown_fields:
        return jsonify({
            "success": False,
            "message": "Unsupported profile field(s): "
                       + ", ".join(sorted(unknown_fields)),
        }), 400

    if not data:
        return jsonify({
            "success": False,
            "message": "Provide at least one profile field to update.",
        }), 400

    profile = db.session.get(UserProfile, user_id)

    if profile is None:
        return jsonify({
            "success": False,
            "message": "Your personal profile could not be found.",
        }), 404

    changes = []
    phone_changed = False

    for field in ("first_name", "last_name"):
        if field in data:
            value = data[field]

            if not isinstance(value, str):
                return jsonify({
                    "success": False,
                    "message": f"{field.replace('_', ' ').title()} must be text.",
                }), 400

            value = value.strip()

            if not value:
                return jsonify({
                    "success": False,
                    "message": f"{field.replace('_', ' ').title()} is required.",
                }), 400

            if len(value) > 100:
                return jsonify({
                    "success": False,
                    "message": f"{field.replace('_', ' ').title()} must not exceed 100 characters.",
                }), 400

            if getattr(profile, field) != value:
                setattr(profile, field, value)
                changes.append(field)

    if "phone" in data:
        raw_phone = data["phone"]

        if raw_phone is not None and not isinstance(raw_phone, str):
            return jsonify({
                "success": False,
                "message": "Phone number must be text or null.",
            }), 400

        phone = (raw_phone or "").strip() or None

        if phone and not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            return jsonify({
                "success": False,
                "message": "Enter a valid international phone number, such as +254712345678.",
            }), 400

        if phone != user.phone:
            if phone:
                duplicate = User.query.filter(
                    User.phone == phone,
                    User.id != user_id,
                ).first()

                if duplicate:
                    return jsonify({
                        "success": False,
                        "message": "That phone number is already linked to another account.",
                    }), 409

            user.phone = phone
            user.phone_verified = False
            phone_changed = True
            changes.append("phone")

    if "preferred_currency" in data:
        currency = data["preferred_currency"]

        if not isinstance(currency, str):
            return jsonify({
                "success": False,
                "message": "Preferred currency must be a currency code.",
            }), 400

        currency = currency.strip().upper()

        if not re.fullmatch(r"[A-Z]{3}", currency):
            return jsonify({
                "success": False,
                "message": "Enter a valid three-letter currency code.",
            }), 400

        currency_record = db.session.get(Currency, currency)

        if currency_record is None or not currency_record.is_active:
            return jsonify({
                "success": False,
                "message": "That currency is unavailable or inactive.",
            }), 400

        if profile.preferred_currency != currency:
            profile.preferred_currency = currency
            changes.append("preferred_currency")

    if not changes:
        return jsonify({
            "success": True,
            "message": "Your profile is already up to date.",
            "changed": False,
        }), 200

    audit = AuditLog(
        workspace_id=None,
        user_id=user.id,
        action="profile_updated",
        resource_type="user_profile",
        resource_id=str(user.id),
        details={
            "changed_fields": changes,
            "phone_verification_reset": phone_changed,
        },
        ip_address=request.remote_addr,
    )

    db.session.add(audit)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "That phone number is already linked to another account.",
        }), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Profile update failed")
        return jsonify({
            "success": False,
            "message": "Your profile could not be updated.",
        }), 500

    return jsonify({
        "success": True,
        "message": "Your profile has been updated.",
        "changed": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "phone": user.phone,
            "phone_verified": user.phone_verified,
        },
        "profile": {
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "timezone": profile.timezone,
            "preferred_currency": profile.preferred_currency,
        },
    }), 200


# BUSINESS WORKSPACE REGISTRATION
# ---------------------------------------------------------

@auth_bp.post("/workspaces/business")
@jwt_required()
def create_business_workspace():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    name = data.get("name")
    business_type = data.get("business_type")
    country = data.get("country")
    currency_code = data.get("currency", "KES")

    if not all(
        isinstance(value, str)
        for value in (name, business_type, country, currency_code)
    ):
        return jsonify({
            "success": False,
            "message": "Business name, type, country and currency must be strings.",
        }), 400

    name = name.strip()
    business_type = business_type.strip()
    country = country.strip()
    currency_code = currency_code.strip().upper()

    if not name or len(name) > 150:
        return jsonify({
            "success": False,
            "message": "Business name is required and must not exceed 150 characters.",
        }), 400

    if not business_type or len(business_type) > 80:
        return jsonify({
            "success": False,
            "message": "Business type is required and must not exceed 80 characters.",
        }), 400

    if not country or len(country) > 100:
        return jsonify({
            "success": False,
            "message": "Country is required and must not exceed 100 characters.",
        }), 400

    if len(currency_code) != 3 or not currency_code.isalpha():
        return jsonify({
            "success": False,
            "message": "Please provide a valid three-letter currency code.",
        }), 400

    optional_fields = {
        "registration_number": 100,
        "business_phone": 30,
        "business_email": 255,
        "address": 255,
    }
    optional_values = {}

    for field, max_length in optional_fields.items():
        value = data.get(field)

        if value is not None and not isinstance(value, str):
            return jsonify({
                "success": False,
                "message": f"{field.replace('_', ' ').capitalize()} must be a string.",
            }), 400

        value = value.strip() if isinstance(value, str) else None

        if value == "":
            value = None

        if value is not None and len(value) > max_length:
            return jsonify({
                "success": False,
                "message": f"{field.replace('_', ' ').capitalize()} must not exceed {max_length} characters.",
            }), 400

        optional_values[field] = value

    if optional_values["business_email"]:
        try:
            optional_values["business_email"] = validate_email(
                optional_values["business_email"],
                check_deliverability=False,
            ).normalized
        except EmailNotValidError:
            return jsonify({
                "success": False,
                "message": "Please provide a valid business email address.",
            }), 400

    currency = db.session.get(Currency, currency_code)

    if currency is None or not currency.is_active:
        return jsonify({
            "success": False,
            "message": "The selected currency is unavailable.",
        }), 400

    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    user = db.session.get(User, user_id)

    if user is None or not user.is_active:
        return jsonify({
            "success": False,
            "message": "Account is unavailable.",
        }), 403

    try:
        workspace = Workspace(
            name=name,
            workspace_type="business",
            functional_currency=currency_code,
        )
        db.session.add(workspace)
        db.session.flush()

        business_profile = BusinessProfile(
            workspace_id=workspace.id,
            business_type=business_type,
            country=country,
            **optional_values,
        )

        membership = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
        )

        ledger_accounts = [
            LedgerAccount(
                workspace_id=workspace.id,
                code=code,
                name=account_name,
                account_class=account_class,
                normal_balance=normal_balance,
                currency_code=currency_code,
                is_system=True,
                is_active=True,
            )
            for code, account_name, account_class, normal_balance
            in DEFAULT_LEDGER_ACCOUNTS
        ]

        audit = AuditLog(
            workspace_id=workspace.id,
            user_id=user.id,
            action="business_workspace_created",
            resource_type="workspace",
            resource_id=str(workspace.id),
            details={
                "business_type": business_type,
                "country": country,
                "currency": currency_code,
            },
            ip_address=request.remote_addr,
        )

        db.session.add_all([
            business_profile,
            membership,
            *ledger_accounts,
            audit,
        ])
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Business workspace created successfully.",
            "workspace": {
                "id": str(workspace.id),
                "name": workspace.name,
                "type": workspace.workspace_type,
                "currency": workspace.functional_currency,
                "role": membership.role,
                "joined_at": membership.joined_at.isoformat(),
                "business_profile": {
                    "business_type": business_profile.business_type,
                    "country": business_profile.country,
                    "registration_number": business_profile.registration_number,
                    "business_phone": business_profile.business_phone,
                    "business_email": business_profile.business_email,
                    "address": business_profile.address,
                },
            },
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The business workspace could not be created because of a conflicting record.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Business workspace creation failed")
        return jsonify({
            "success": False,
            "message": "Business workspace could not be created. Please try again.",
        }), 500


# ---------------------------------------------------------------------------
# Workspace membership and invitations
# ---------------------------------------------------------------------------


@auth_bp.post("/workspaces/<workspace_id>/invitations/<invitation_id>/resend")
@jwt_required()
def resend_workspace_invitation(workspace_id, invitation_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    workspace_error = _business_workspace_access_error(
        membership.workspace_id
    )
    if workspace_error:
        return workspace_error

    if membership.role not in ("owner", "admin"):
        return jsonify({"success": False, "message": "Permission denied."}), 403

    try:
        invitation_uuid = UUID(str(invitation_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid invitation ID."}), 400

    invitation = WorkspaceInvitation.query.filter_by(
        id=invitation_uuid,
        workspace_id=membership.workspace_id,
    ).with_for_update().first()

    if invitation is None or invitation.status != "pending":
        return jsonify({
            "success": False,
            "message": "Pending invitation not found.",
        }), 404

    if membership.role == "admin" and invitation.role not in ("member", "viewer"):
        return jsonify({
            "success": False,
            "message": "Admins cannot manage admin invitations.",
        }), 403

    raw_token = secrets.token_urlsafe(32)
    invitation.token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    try:
        db.session.add(AuditLog(
            workspace_id=membership.workspace_id,
            user_id=user.id,
            action="workspace_invitation_resent",
            resource_type="workspace_invitation",
            resource_id=str(invitation.id),
            details={"email": invitation.email, "role": invitation.role},
            ip_address=request.remote_addr,
        ))
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Invitation renewed. Share the new token securely.",
            "invitation": {
                "id": str(invitation.id),
                "email": invitation.email,
                "role": invitation.role,
                "expires_at": invitation.expires_at.isoformat(),
                "token": raw_token,
            },
            "delivery": "manual",
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace invitation resend failed")
        return jsonify({
            "success": False,
            "message": "Could not renew the invitation.",
        }), 500


@auth_bp.delete("/workspaces/<workspace_id>/invitations/<invitation_id>")
@jwt_required()
def revoke_workspace_invitation(workspace_id, invitation_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    if membership.role not in ("owner", "admin"):
        return jsonify({"success": False, "message": "Permission denied."}), 403

    try:
        invitation_uuid = UUID(str(invitation_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid invitation ID."}), 400

    invitation = WorkspaceInvitation.query.filter_by(
        id=invitation_uuid,
        workspace_id=membership.workspace_id,
    ).with_for_update().first()

    if invitation is None or invitation.status != "pending":
        return jsonify({
            "success": False,
            "message": "Pending invitation not found.",
        }), 404

    if membership.role == "admin" and invitation.role not in ("member", "viewer"):
        return jsonify({
            "success": False,
            "message": "Admins cannot revoke admin invitations.",
        }), 403

    try:
        invitation.status = "revoked"
        invitation.revoked_at = datetime.now(timezone.utc)

        db.session.add(AuditLog(
            workspace_id=membership.workspace_id,
            user_id=user.id,
            action="workspace_invitation_revoked",
            resource_type="workspace_invitation",
            resource_id=str(invitation.id),
            details={"email": invitation.email, "role": invitation.role},
            ip_address=request.remote_addr,
        ))
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Invitation revoked.",
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace invitation revocation failed")
        return jsonify({
            "success": False,
            "message": "Could not revoke the invitation.",
        }), 500


@auth_bp.patch("/workspaces/<workspace_id>/members/<member_user_id>")
@jwt_required()
def update_workspace_member_role(workspace_id, member_user_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    workspace_error = _business_workspace_access_error(
        membership.workspace_id
    )
    if workspace_error:
        return workspace_error

    if membership.role not in ("owner", "admin"):
        return jsonify({"success": False, "message": "Permission denied."}), 403

    try:
        target_user_id = UUID(str(member_user_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid member ID."}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("role"), str):
        return jsonify({
            "success": False,
            "message": "A valid role is required.",
        }), 400

    new_role = data["role"].strip().lower()
    if new_role not in ("admin", "member", "viewer"):
        return jsonify({
            "success": False,
            "message": "Role must be admin, member, or viewer.",
        }), 400

    target = WorkspaceMember.query.filter_by(
        workspace_id=membership.workspace_id,
        user_id=target_user_id,
    ).with_for_update().first()

    if target is None:
        return jsonify({"success": False, "message": "Member not found."}), 404

    if target.role == "owner":
        return jsonify({
            "success": False,
            "message": "The workspace owner cannot be changed here.",
        }), 403

    if membership.role == "admin":
        if target.role not in ("member", "viewer") or new_role == "admin":
            return jsonify({
                "success": False,
                "message": "Admins can only manage members and viewers.",
            }), 403

    if target.role == new_role:
        return jsonify({
            "success": True,
            "message": "Member already has that role.",
            "role": target.role,
        }), 200

    old_role = target.role

    try:
        target.role = new_role
        db.session.add(AuditLog(
            workspace_id=membership.workspace_id,
            user_id=user.id,
            action="workspace_member_role_changed",
            resource_type="workspace_member",
            resource_id=str(target.user_id),
            details={"old_role": old_role, "new_role": new_role},
            ip_address=request.remote_addr,
        ))
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Member role updated.",
            "user_id": str(target.user_id),
            "role": target.role,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace member role update failed")
        return jsonify({
            "success": False,
            "message": "Could not update the member role.",
        }), 500


@auth_bp.delete("/workspaces/<workspace_id>/members/<member_user_id>")
@jwt_required()
def remove_workspace_member(workspace_id, member_user_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    if membership.role not in ("owner", "admin"):
        return jsonify({"success": False, "message": "Permission denied."}), 403

    try:
        target_user_id = UUID(str(member_user_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid member ID."}), 400

    target = WorkspaceMember.query.filter_by(
        workspace_id=membership.workspace_id,
        user_id=target_user_id,
    ).with_for_update().first()

    if target is None:
        return jsonify({"success": False, "message": "Member not found."}), 404

    if target.role == "owner":
        return jsonify({
            "success": False,
            "message": "The workspace owner cannot be removed.",
        }), 403

    if membership.role == "admin" and target.role not in ("member", "viewer"):
        return jsonify({
            "success": False,
            "message": "Admins can only remove members and viewers.",
        }), 403

    removed_role = target.role

    try:
        db.session.add(AuditLog(
            workspace_id=membership.workspace_id,
            user_id=user.id,
            action="workspace_member_removed",
            resource_type="workspace_member",
            resource_id=str(target.user_id),
            details={"removed_role": removed_role},
            ip_address=request.remote_addr,
        ))
        db.session.delete(target)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Workspace member removed.",
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace member removal failed")
        return jsonify({
            "success": False,
            "message": "Could not remove the workspace member.",
        }), 500



def _business_workspace_access_error(workspace_id):
    """Prevent collaboration and ownership operations on private workspaces."""
    workspace = db.session.get(Workspace, workspace_id)

    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404

    if workspace.workspace_type != "business":
        return jsonify({
            "success": False,
            "message": "Personal workspaces are private. Create a Business/SACCO workspace to collaborate.",
        }), 403

    return None


@auth_bp.post("/workspaces/<workspace_id>/transfer-ownership")
@jwt_required()
def transfer_workspace_ownership(workspace_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    workspace_error = _business_workspace_access_error(
        membership.workspace_id
    )
    if workspace_error:
        return workspace_error

    if membership.role != "owner":
        return jsonify({
            "success": False,
            "message": "Only the workspace owner can transfer ownership.",
        }), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    try:
        target_user_id = UUID(str(data.get("member_user_id", "")))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid recipient member ID.",
        }), 400

    if target_user_id == user.id:
        return jsonify({
            "success": False,
            "message": "You are already the workspace owner.",
        }), 400

    try:
        current_owner = WorkspaceMember.query.filter_by(
            workspace_id=membership.workspace_id,
            user_id=user.id,
        ).with_for_update().first()

        target = WorkspaceMember.query.filter_by(
            workspace_id=membership.workspace_id,
            user_id=target_user_id,
        ).with_for_update().first()

        if current_owner is None or current_owner.role != "owner":
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "You are no longer the workspace owner.",
            }), 403

        if target is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "The selected workspace member was not found.",
            }), 404

        if target.role != "admin":
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Ownership can only be transferred to an existing admin.",
            }), 409

        previous_owner_id = str(current_owner.user_id)
        new_owner_id = str(target.user_id)

        current_owner.role = "admin"
        target.role = "owner"

        db.session.add(AuditLog(
            workspace_id=membership.workspace_id,
            user_id=user.id,
            action="workspace_ownership_transferred",
            resource_type="workspace",
            resource_id=str(membership.workspace_id),
            details={
                "previous_owner_user_id": previous_owner_id,
                "new_owner_user_id": new_owner_id,
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Workspace ownership transferred successfully.",
            "previous_owner_user_id": previous_owner_id,
            "new_owner_user_id": new_owner_id,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace ownership transfer failed")
        return jsonify({
            "success": False,
            "message": "Could not transfer workspace ownership.",
        }), 500


def _workspace_invitation_context(workspace_id):
    """Return the authenticated user and their workspace membership."""
    try:
        user_id = UUID(get_jwt_identity())
        workspace_uuid = UUID(str(workspace_id))
    except (ValueError, TypeError, AttributeError):
        return None, None, (jsonify({
            "success": False,
            "message": "Invalid user or workspace identity.",
        }), 400)

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return None, None, (jsonify({
            "success": False,
            "message": "Account is unavailable.",
        }), 403)

    membership = WorkspaceMember.query.filter_by(
        workspace_id=workspace_uuid,
        user_id=user_id,
    ).first()

    if membership is None:
        return None, None, (jsonify({
            "success": False,
            "message": "You are not a member of this workspace.",
        }), 403)

    return user, membership, None


@auth_bp.get("/workspaces/<workspace_id>/members")
@jwt_required()
def list_workspace_members(workspace_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    rows = (
        db.session.query(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .filter(WorkspaceMember.workspace_id == membership.workspace_id)
        .order_by(WorkspaceMember.joined_at.asc())
        .all()
    )

    return jsonify({
        "success": True,
        "members": [
            {
                "user_id": str(member.user_id),
                "email": account.email,
                "role": member.role,
                "joined_at": member.joined_at.isoformat(),
            }
            for member, account in rows
        ],
    }), 200


@auth_bp.get("/workspaces/<workspace_id>/invitations")
@jwt_required()
def list_workspace_invitations(workspace_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    if membership.role not in ("owner", "admin"):
        return jsonify({
            "success": False,
            "message": "Only workspace owners and admins can view invitations.",
        }), 403

    invitations = (
        WorkspaceInvitation.query
        .filter_by(workspace_id=membership.workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc())
        .all()
    )

    now = datetime.now(timezone.utc)

    invitation_items = []

    for invitation in invitations:
        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        status = invitation.status
        if status == "pending" and expires_at <= now:
            status = "expired"

        invitation_items.append({
            "id": str(invitation.id),
            "email": invitation.email,
            "role": invitation.role,
            "status": status,
            "expires_at": expires_at.isoformat(),
            "created_at": invitation.created_at.isoformat(),
        })

    return jsonify({
        "success": True,
        "invitations": invitation_items,
    }), 200


@auth_bp.post("/workspaces/<workspace_id>/invitations")
@jwt_required()
def create_workspace_invitation(workspace_id):
    user, membership, error = _workspace_invitation_context(workspace_id)
    if error:
        return error

    workspace_error = _business_workspace_access_error(
        membership.workspace_id
    )
    if workspace_error:
        return workspace_error

    if membership.role not in ("owner", "admin"):
        return jsonify({
            "success": False,
            "message": "Only workspace owners and admins can invite members.",
        }), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    email = data.get("email")
    role = data.get("role")

    if not isinstance(email, str) or not isinstance(role, str):
        return jsonify({
            "success": False,
            "message": "Email and role are required.",
        }), 400

    try:
        email = validate_email(
            email.strip(),
            check_deliverability=False,
        ).normalized.lower()
    except (EmailNotValidError, ValueError):
        return jsonify({
            "success": False,
            "message": "Please provide a valid email address.",
        }), 400

    role = role.strip().lower()
    allowed_roles = {"admin", "member", "viewer"}

    if role not in allowed_roles:
        return jsonify({
            "success": False,
            "message": "Invitation role must be admin, member, or viewer.",
        }), 400

    if membership.role == "admin" and role not in {"member", "viewer"}:
        return jsonify({
            "success": False,
            "message": "Admins can only invite members and viewers.",
        }), 403

    existing_user = User.query.filter(
        db.func.lower(User.email) == email
    ).first()

    if existing_user:
        existing_membership = WorkspaceMember.query.filter_by(
            workspace_id=membership.workspace_id,
            user_id=existing_user.id,
        ).first()

        if existing_membership:
            return jsonify({
                "success": False,
                "message": "This user is already a workspace member.",
            }), 409

    now = datetime.now(timezone.utc)

    existing_invitation = WorkspaceInvitation.query.filter(
        WorkspaceInvitation.workspace_id == membership.workspace_id,
        db.func.lower(WorkspaceInvitation.email) == email,
        WorkspaceInvitation.status == "pending",
        WorkspaceInvitation.expires_at > now,
    ).first()

    if existing_invitation:
        return jsonify({
            "success": False,
            "message": "An active invitation already exists for this email.",
        }), 409

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    invitation = WorkspaceInvitation(
        workspace_id=membership.workspace_id,
        email=email,
        role=role,
        invited_by=user.id,
        token_hash=token_hash,
        status="pending",
        expires_at=now + timedelta(days=7),
    )

    audit = AuditLog(
        workspace_id=membership.workspace_id,
        user_id=user.id,
        action="workspace_invitation_created",
        resource_type="workspace_invitation",
        resource_id=None,
        details={
            "email": email,
            "role": role,
        },
        ip_address=request.remote_addr,
    )

    try:
        db.session.add(invitation)
        db.session.flush()
        audit.resource_id = str(invitation.id)
        db.session.add(audit)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Invitation created. Share the token securely with the invitee.",
            "invitation": {
                "id": str(invitation.id),
                "email": invitation.email,
                "role": invitation.role,
                "expires_at": invitation.expires_at.isoformat(),
                "token": raw_token,
            },
            "delivery": "manual",
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "An invitation conflict occurred. Please try again.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace invitation creation failed")
        return jsonify({
            "success": False,
            "message": "Could not create the invitation. Please try again.",
        }), 500


@auth_bp.post("/invitations/accept")
@jwt_required()
def accept_workspace_invitation():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    raw_token = data.get("token")
    if not isinstance(raw_token, str) or not raw_token.strip():
        return jsonify({
            "success": False,
            "message": "Invitation token is required.",
        }), 400

    try:
        user_id = UUID(get_jwt_identity())
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        return jsonify({
            "success": False,
            "message": "Account is unavailable.",
        }), 403

    if not user.email_verified:
        return jsonify({
            "success": False,
            "message": "Verify your account email before accepting an invitation.",
        }), 403

    token_hash = hashlib.sha256(
        raw_token.strip().encode("utf-8")
    ).hexdigest()

    invitation = WorkspaceInvitation.query.filter_by(
        token_hash=token_hash,
    ).with_for_update().first()

    if invitation is None or invitation.status != "pending":
        return jsonify({
            "success": False,
            "message": "This invitation is invalid or has already been used.",
        }), 404

    now = datetime.now(timezone.utc)
    expires_at = invitation.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        return jsonify({
            "success": False,
            "message": "This invitation has expired.",
        }), 410

    if user.email.strip().lower() != invitation.email.strip().lower():
        return jsonify({
            "success": False,
            "message": "This invitation was sent to a different email address.",
        }), 403

    existing_membership = WorkspaceMember.query.filter_by(
        workspace_id=invitation.workspace_id,
        user_id=user.id,
    ).first()

    if existing_membership:
        return jsonify({
            "success": False,
            "message": "You are already a member of this workspace.",
        }), 409

    workspace = db.session.get(Workspace, invitation.workspace_id)
    if workspace is None:
        return jsonify({
            "success": False,
            "message": "The invited workspace no longer exists.",
        }), 404

    if workspace.workspace_type != "business":
        return jsonify({
            "success": False,
            "message": "Personal workspaces are private. This invitation cannot be accepted.",
        }), 403

    try:
        membership = WorkspaceMember(
            workspace_id=invitation.workspace_id,
            user_id=user.id,
            role=invitation.role,
        )
        invitation.status = "accepted"
        invitation.accepted_at = now

        db.session.add(membership)
        db.session.add(AuditLog(
            workspace_id=invitation.workspace_id,
            user_id=user.id,
            action="workspace_invitation_accepted",
            resource_type="workspace_invitation",
            resource_id=str(invitation.id),
            details={
                "email": user.email,
                "role": invitation.role,
            },
            ip_address=request.remote_addr,
        ))
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "You have joined the workspace.",
            "workspace": {
                "id": str(workspace.id),
                "name": workspace.name,
                "role": membership.role,
                "currency": workspace.functional_currency,
            },
        }), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "You may already be a member of this workspace.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Workspace invitation acceptance failed")
        return jsonify({
            "success": False,
            "message": "Could not accept the invitation. Please try again.",
        }), 500
