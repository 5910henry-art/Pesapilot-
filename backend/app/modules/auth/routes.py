import hashlib
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from flask import current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import (
    User,
    UserProfile,
    Workspace,
    WorkspaceMember,
    Currency,
    LedgerAccount,
    AuditLog,
    RefreshToken,
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
        access_token = create_access_token(
            identity=str(user.id)
        )

        refresh_token = create_refresh_token(
            identity=str(user.id)
        )

        token_record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
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

        new_refresh_token = create_refresh_token(
            identity=str(user.id)
        )

        new_record = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(new_refresh_token),
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
            identity=str(user.id)
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

        if record.revoked_at is not None or record.expires_at <= now:
            return jsonify({
                "success": False,
                "message": "Refresh token is expired or revoked.",
            }), 401

        record.revoked_at = now
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
