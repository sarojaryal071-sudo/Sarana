"""
tests/test_native_location.py -- actions/native_location.py (the native
Windows desktop location adapter) in isolation. The REAL winsdk/WinRT
Geolocator IS available on this machine (confirmed live -- see this
stage's own completion report for the actual real-machine verification,
performed separately, not repeated here on every test run) but every
test in this file mocks `actions.native_location.Geolocator`/
`GeolocationAccessStatus`/`PositionStatus` directly, per this stage's
own explicit instruction: "isolate the provider behind a small adapter
and test the real provider separately." This file exhaustively exercises
the adapter's own mapping/validation logic (every documented Windows
state, every malformed-result shape) without ever depending on this
machine's actual location hardware/permission state, so it stays
deterministic and fast regardless of what machine/CI it runs on.

Run with:
    .venv/Scripts/python.exe -m tests.test_native_location
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import actions.native_location as nl


def _fake_coordinate(lat=60.17, lon=24.94, acc=50.0, ts=None):
    coord = MagicMock()
    coord.latitude = lat
    coord.longitude = lon
    coord.accuracy = acc
    coord.timestamp = ts or datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
    return coord


def _fake_position(coord):
    pos = MagicMock()
    pos.coordinate = coord
    return pos


# ── platform support ────────────────────────────────────────────────────

def test_is_platform_supported_reflects_the_real_winsdk_import() -> None:
    # Real, not mocked -- this machine genuinely has a working winsdk
    # import (confirmed via this stage's own real-machine verification).
    assert nl.is_platform_supported() is True
    print("test_is_platform_supported_reflects_the_real_winsdk_import: PASS")


def test_unavailable_platform_raises_before_touching_any_winrt_object() -> None:
    with patch.object(nl, "_WINSDK_AVAILABLE", False):
        try:
            asyncio.run(nl.request_access())
            raised = False
        except nl.NativeLocationUnavailable:
            raised = True
        assert raised

        try:
            asyncio.run(nl.get_current_location())
            raised = False
        except nl.NativeLocationUnavailable:
            raised = True
        assert raised
    print("test_unavailable_platform_raises_before_touching_any_winrt_object: PASS")


# ── request_access() / permission mapping ────────────────────────────────

def test_request_access_maps_allowed() -> None:
    with patch.object(nl, "Geolocator") as m_geo, \
         patch.object(nl, "GeolocationAccessStatus") as m_status:
        m_status.ALLOWED, m_status.DENIED, m_status.UNSPECIFIED = "ALLOWED", "DENIED", "UNSPECIFIED"
        m_geo.request_access_async = AsyncMock(return_value="ALLOWED")
        result = asyncio.run(nl.request_access())
    assert result == "allowed"
    print("test_request_access_maps_allowed: PASS")


def test_request_access_maps_denied() -> None:
    with patch.object(nl, "Geolocator") as m_geo, \
         patch.object(nl, "GeolocationAccessStatus") as m_status:
        m_status.ALLOWED, m_status.DENIED, m_status.UNSPECIFIED = "ALLOWED", "DENIED", "UNSPECIFIED"
        m_geo.request_access_async = AsyncMock(return_value="DENIED")
        result = asyncio.run(nl.request_access())
    assert result == "denied"
    print("test_request_access_maps_denied: PASS")


def test_request_access_maps_unspecified_never_silently_folded_into_allowed_or_denied() -> None:
    with patch.object(nl, "Geolocator") as m_geo, \
         patch.object(nl, "GeolocationAccessStatus") as m_status:
        m_status.ALLOWED, m_status.DENIED, m_status.UNSPECIFIED = "ALLOWED", "DENIED", "UNSPECIFIED"
        m_geo.request_access_async = AsyncMock(return_value="UNSPECIFIED")
        result = asyncio.run(nl.request_access())
    assert result == "unspecified"
    print("test_request_access_maps_unspecified_never_silently_folded_into_allowed_or_denied: PASS")


# ── get_current_location(): location_status pre-check ────────────────────

def test_windows_location_disabled_system_wide_is_reported_honestly() -> None:
    with patch.object(nl, "Geolocator") as m_geo_cls, \
         patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA = "DISABLED", "NOT_AVAILABLE", "NO_DATA"
        instance = MagicMock()
        instance.location_status = "DISABLED"
        m_geo_cls.return_value = instance
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationDisabled)
    instance.get_geoposition_async.assert_not_called()
    print("test_windows_location_disabled_system_wide_is_reported_honestly: PASS")


def test_no_provider_available_is_reported_as_no_data_not_a_generic_error() -> None:
    with patch.object(nl, "Geolocator") as m_geo_cls, \
         patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA = "DISABLED", "NOT_AVAILABLE", "NO_DATA"
        instance = MagicMock()
        instance.location_status = "NOT_AVAILABLE"
        m_geo_cls.return_value = instance
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationNoData)
    print("test_no_provider_available_is_reported_as_no_data_not_a_generic_error: PASS")


# ── get_current_location(): the real fetch and its failure modes ────────

def _ready_geolocator(get_geoposition_async):
    instance = MagicMock()
    instance.location_status = "READY"
    instance.get_geoposition_async = get_geoposition_async
    return instance


def test_successful_fix_returns_the_documented_shape() -> None:
    coord = _fake_coordinate(lat=60.17, lon=24.94, acc=42.0)
    with patch.object(nl, "Geolocator") as m_geo_cls, \
         patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA = "DISABLED", "NOT_AVAILABLE", "NO_DATA"
        m_ps.READY = "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=_fake_position(coord)))
        fix = asyncio.run(nl.get_current_location())
    assert fix["latitude"] == 60.17
    assert fix["longitude"] == 24.94
    assert fix["accuracy"] == 42.0
    assert fix["timestamp"].tzinfo is not None
    print("test_successful_fix_returns_the_documented_shape: PASS")


def test_missing_accuracy_is_reported_as_none_never_guessed() -> None:
    coord = _fake_coordinate(acc=None)
    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=_fake_position(coord)))
        fix = asyncio.run(nl.get_current_location())
    assert fix["accuracy"] is None
    print("test_missing_accuracy_is_reported_as_none_never_guessed: PASS")


def test_negative_accuracy_is_downgraded_to_none_not_treated_as_a_fatal_error() -> None:
    coord = _fake_coordinate(acc=-5.0)
    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=_fake_position(coord)))
        fix = asyncio.run(nl.get_current_location())
    assert fix["accuracy"] is None
    assert fix["latitude"] == 60.17  # the position itself is still honored
    print("test_negative_accuracy_is_downgraded_to_none_not_treated_as_a_fatal_error: PASS")


def test_implausible_coordinate_is_a_real_error_never_returned() -> None:
    coord = _fake_coordinate(lat=999.0, lon=24.94)
    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=_fake_position(coord)))
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert raised is not None and not isinstance(raised, (nl.NativeLocationDisabled, nl.NativeLocationNoData))
    print("test_implausible_coordinate_is_a_real_error_never_returned: PASS")


def test_null_position_is_no_data_not_a_crash() -> None:
    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=None))
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationNoData)
    print("test_null_position_is_no_data_not_a_crash: PASS")


def test_null_coordinate_is_no_data_not_a_crash() -> None:
    pos = MagicMock()
    pos.coordinate = None
    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(AsyncMock(return_value=pos))
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationNoData)
    print("test_null_coordinate_is_no_data_not_a_crash: PASS")


def test_timeout_is_reported_honestly_never_as_a_fix() -> None:
    async def _hangs_forever(*_a, **_kw):
        await asyncio.sleep(999)

    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(_hangs_forever)
        try:
            asyncio.run(nl.get_current_location(timeout_s=0.05))
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationTimeout)
    print("test_timeout_is_reported_honestly_never_as_a_fix: PASS")


def test_permission_error_during_fetch_is_reported_as_denied() -> None:
    async def _denied(*_a, **_kw):
        raise PermissionError("access denied")

    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(_denied)
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationPermissionDenied)
    print("test_permission_error_during_fetch_is_reported_as_denied: PASS")


def test_e_accessdenied_hresult_is_mapped_to_permission_denied() -> None:
    async def _os_error(*_a, **_kw):
        err = OSError("access is denied")
        err.winerror = -2147024891  # E_ACCESSDENIED -- a standard, well-documented COM HRESULT
        raise err

    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(_os_error)
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert isinstance(raised, nl.NativeLocationPermissionDenied)
    print("test_e_accessdenied_hresult_is_mapped_to_permission_denied: PASS")


def test_an_unrecognized_oserror_falls_through_to_the_generic_error_never_miscategorized() -> None:
    # This project's own discipline: never assert an unverified HRESULT
    # mapping as confirmed -- see native_location.py's own comment on
    # this exact point.
    async def _os_error(*_a, **_kw):
        err = OSError("some other winrt failure")
        err.winerror = -123456789
        raise err

    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(_os_error)
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert type(raised) is nl.NativeLocationError
    print("test_an_unrecognized_oserror_falls_through_to_the_generic_error_never_miscategorized: PASS")


def test_a_completely_unexpected_exception_is_still_caught_never_propagates_raw() -> None:
    async def _boom(*_a, **_kw):
        raise RuntimeError("something totally unexpected")

    with patch.object(nl, "Geolocator") as m_geo_cls, patch.object(nl, "PositionStatus") as m_ps:
        m_ps.DISABLED, m_ps.NOT_AVAILABLE, m_ps.NO_DATA, m_ps.READY = "D", "NA", "ND", "READY"
        m_geo_cls.return_value = _ready_geolocator(_boom)
        try:
            asyncio.run(nl.get_current_location())
            raised = None
        except nl.NativeLocationError as e:
            raised = e
    assert raised is not None
    print("test_a_completely_unexpected_exception_is_still_caught_never_propagates_raw: PASS")


def _run() -> None:
    test_is_platform_supported_reflects_the_real_winsdk_import()
    test_unavailable_platform_raises_before_touching_any_winrt_object()
    test_request_access_maps_allowed()
    test_request_access_maps_denied()
    test_request_access_maps_unspecified_never_silently_folded_into_allowed_or_denied()
    test_windows_location_disabled_system_wide_is_reported_honestly()
    test_no_provider_available_is_reported_as_no_data_not_a_generic_error()
    test_successful_fix_returns_the_documented_shape()
    test_missing_accuracy_is_reported_as_none_never_guessed()
    test_negative_accuracy_is_downgraded_to_none_not_treated_as_a_fatal_error()
    test_implausible_coordinate_is_a_real_error_never_returned()
    test_null_position_is_no_data_not_a_crash()
    test_null_coordinate_is_no_data_not_a_crash()
    test_timeout_is_reported_honestly_never_as_a_fix()
    test_permission_error_during_fetch_is_reported_as_denied()
    test_e_accessdenied_hresult_is_mapped_to_permission_denied()
    test_an_unrecognized_oserror_falls_through_to_the_generic_error_never_miscategorized()
    test_a_completely_unexpected_exception_is_still_caught_never_propagates_raw()
    print("\nAll native_location tests passed.")


if __name__ == "__main__":
    _run()
