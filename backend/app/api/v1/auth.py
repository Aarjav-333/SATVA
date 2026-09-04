"""/auth -- phone OTP for consumers, password login for dashboard roles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import Principal, auth_rate_limit, get_current_principal, write_audit
from app.db.session import get_db
from app.schemas.auth import (
    AuthResponse,
    DeviceOut,
    DeviceRegisterRequest,
    OtpStartResponse,
    OtpVerifyRequest,
    PasswordLoginRequest,
    PhoneRequest,
    RefreshRequest,
    TokenPair,
    UserOut,
)
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/otp/start",
    response_model=OtpStartResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Request a phone verification code",
)
def start_otp(payload: PhoneRequest, db: Session = Depends(get_db)) -> OtpStartResponse:
    challenge, dev_code, provider = AuthService(db).start_otp(payload.phone_number)
    db.commit()

    message = (
        "No SMS provider is configured in this environment, so the code is returned here "
        "for local testing. It is never returned when a real provider is configured."
        if dev_code
        else "A verification code has been sent to your phone."
    )
    return OtpStartResponse(
        challenge_id=challenge.id,
        expires_at=challenge.expires_at,
        provider=provider.name,
        dev_code=dev_code,
        message=message,
    )


@router.post(
    "/otp/verify",
    response_model=AuthResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Verify a phone code and sign in",
)
def verify_otp(payload: OtpVerifyRequest, db: Session = Depends(get_db)) -> AuthResponse:
    service = AuthService(db)
    user, device = service.verify_otp(
        challenge_id=payload.challenge_id,
        phone_number=payload.phone_number,
        code=payload.code,
        device_identifier=payload.device_identifier,
        app_version=payload.app_version,
        model_name=payload.model_name,
    )
    access, refresh, expires_in = service.issue_tokens(user)
    db.commit()
    return AuthResponse(
        user=UserOut.model_validate(user),
        tokens=TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in),
        device_pseudonym=device.device_pseudonym,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Sign in with email and password (officer, retailer, farmer, admin)",
)
def password_login(
    payload: PasswordLoginRequest, request: Request, db: Session = Depends(get_db)
) -> AuthResponse:
    service = AuthService(db)
    user = service.password_login(payload.email, payload.password)
    access, refresh, expires_in = service.issue_tokens(user)
    write_audit(
        db,
        action="auth.login",
        principal=Principal(user=user, role=user.role),
        object_type="user",
        object_id=str(user.id),
        request=request,
    )
    db.commit()
    return AuthResponse(
        user=UserOut.model_validate(user),
        tokens=TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in),
    )


@router.post("/refresh", response_model=TokenPair, summary="Exchange a refresh token")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    _, access, new_refresh, expires_in = AuthService(db).refresh(payload.refresh_token)
    db.commit()
    return TokenPair(access_token=access, refresh_token=new_refresh, expires_in=expires_in)


@router.get("/me", response_model=UserOut, summary="The signed-in account")
def me(principal: Principal = Depends(get_current_principal)) -> UserOut:
    return UserOut.model_validate(principal.user)


@router.post(
    "/devices",
    response_model=DeviceOut,
    summary="Register this handset",
    description=(
        "Exchanges a raw device identifier for a keyed pseudonym. The raw identifier is "
        "never stored: only the pseudonym reaches scan records, which is what lets SATVA "
        "Watch count independent devices without holding installation identifiers."
    ),
)
def register_device(
    payload: DeviceRegisterRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> DeviceOut:
    device = AuthService(db).register_device(
        user=principal.user,
        device_identifier=payload.device_identifier,
        platform=payload.platform,
        app_version=payload.app_version,
        model_name=payload.model_name,
    )
    db.commit()
    return DeviceOut.model_validate(device)
