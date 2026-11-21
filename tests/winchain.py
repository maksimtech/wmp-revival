"""Ask the Windows chain engine (crypt32) to judge a certificate, as Schannel clients do.

``CertGetCertificateChain`` builds and checks the chain (name constraints included);
``CertVerifyCertificateChainPolicy`` with the SSL policy then checks it for a server name.
Test helper only: Windows-only, no network (cached URL retrieval only).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

X509_ASN_ENCODING = 0x1
PKCS_7_ASN_ENCODING = 0x10000
ENCODING = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
CERT_STORE_PROV_MEMORY = 2
CERT_STORE_ADD_ALWAYS = 4
CERT_CHAIN_CACHE_ONLY_URL_RETRIEVAL = 0x4
CERT_CHAIN_DISABLE_AUTH_ROOT_AUTO_UPDATE = 0x100
HCCE_LOCAL_MACHINE = 1
CERT_CHAIN_POLICY_SSL = 4
AUTHTYPE_SERVER = 2

CERT_TRUST_INVALID_NAME_CONSTRAINTS = 0x00000800
CERT_TRUST_HAS_NOT_SUPPORTED_NAME_CONSTRAINT = 0x00001000
CERT_TRUST_HAS_NOT_DEFINED_NAME_CONSTRAINT = 0x00002000
CERT_TRUST_HAS_NOT_PERMITTED_NAME_CONSTRAINT = 0x00004000
CERT_TRUST_HAS_EXCLUDED_NAME_CONSTRAINT = 0x00008000
NAME_CONSTRAINT_ERRORS = (
    CERT_TRUST_INVALID_NAME_CONSTRAINTS
    | CERT_TRUST_HAS_NOT_SUPPORTED_NAME_CONSTRAINT
    | CERT_TRUST_HAS_NOT_DEFINED_NAME_CONSTRAINT
    | CERT_TRUST_HAS_NOT_PERMITTED_NAME_CONSTRAINT
    | CERT_TRUST_HAS_EXCLUDED_NAME_CONSTRAINT
)
CERT_E_CN_NO_MATCH = 0x800B010F


class CERT_TRUST_STATUS(ctypes.Structure):  # noqa: N801 - Windows names
    _fields_ = [("dwErrorStatus", wintypes.DWORD), ("dwInfoStatus", wintypes.DWORD)]


class CERT_CHAIN_CONTEXT(ctypes.Structure):  # noqa: N801
    # Only the leading fields are read.
    _fields_ = [("cbSize", wintypes.DWORD), ("TrustStatus", CERT_TRUST_STATUS)]


class CERT_ENHKEY_USAGE(ctypes.Structure):  # noqa: N801
    _fields_ = [("cUsageIdentifier", wintypes.DWORD), ("rgpszUsageIdentifier", ctypes.c_void_p)]


class CERT_USAGE_MATCH(ctypes.Structure):  # noqa: N801
    _fields_ = [("dwType", wintypes.DWORD), ("Usage", CERT_ENHKEY_USAGE)]


class CERT_CHAIN_PARA(ctypes.Structure):  # noqa: N801
    _fields_ = [("cbSize", wintypes.DWORD), ("RequestedUsage", CERT_USAGE_MATCH)]


class CERT_CHAIN_ENGINE_CONFIG(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hRestrictedRoot", wintypes.HANDLE),
        ("hRestrictedTrust", wintypes.HANDLE),
        ("hRestrictedOther", wintypes.HANDLE),
        ("cAdditionalStore", wintypes.DWORD),
        ("rghAdditionalStore", ctypes.c_void_p),
        ("dwFlags", wintypes.DWORD),
        ("dwUrlRetrievalTimeout", wintypes.DWORD),
        ("MaximumCachedCertificates", wintypes.DWORD),
        ("CycleDetectionModulus", wintypes.DWORD),
        ("hExclusiveRoot", wintypes.HANDLE),
        ("hExclusiveTrustedPeople", wintypes.HANDLE),
        ("dwExclusiveFlags", wintypes.DWORD),
    ]


class SSL_EXTRA_CERT_CHAIN_POLICY_PARA(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwAuthType", wintypes.DWORD),
        ("fdwChecks", wintypes.DWORD),
        ("pwszServerName", wintypes.LPCWSTR),
    ]


class CERT_CHAIN_POLICY_PARA(ctypes.Structure):  # noqa: N801
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("pvExtraPolicyPara", ctypes.c_void_p)]


class CERT_CHAIN_POLICY_STATUS(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwError", wintypes.DWORD),
        ("lChainIndex", wintypes.LONG),
        ("lElementIndex", wintypes.LONG),
        ("pvExtraPolicyStatus", ctypes.c_void_p),
    ]


def _crypt32() -> ctypes.WinDLL:
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    dll.CertCreateCertificateContext.restype = ctypes.c_void_p
    dll.CertCreateCertificateContext.argtypes = [wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD]
    dll.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    dll.CertOpenStore.restype = wintypes.HANDLE
    dll.CertOpenStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    dll.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
    dll.CertAddEncodedCertificateToStore.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_char_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    dll.CertCloseStore.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.CertCreateCertificateChainEngine.restype = wintypes.BOOL
    dll.CertCreateCertificateChainEngine.argtypes = [ctypes.POINTER(CERT_CHAIN_ENGINE_CONFIG), ctypes.c_void_p]
    dll.CertFreeCertificateChainEngine.argtypes = [wintypes.HANDLE]
    dll.CertGetCertificateChain.restype = wintypes.BOOL
    dll.CertGetCertificateChain.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.HANDLE,
        ctypes.POINTER(CERT_CHAIN_PARA),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(CERT_CHAIN_CONTEXT)),
    ]
    dll.CertFreeCertificateChain.argtypes = [ctypes.POINTER(CERT_CHAIN_CONTEXT)]
    dll.CertVerifyCertificateChainPolicy.restype = wintypes.BOOL
    dll.CertVerifyCertificateChainPolicy.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(CERT_CHAIN_CONTEXT),
        ctypes.POINTER(CERT_CHAIN_POLICY_PARA),
        ctypes.POINTER(CERT_CHAIN_POLICY_STATUS),
    ]
    return dll


@dataclass(frozen=True)
class Verdict:
    chain_errors: int  # CERT_TRUST_STATUS.dwErrorStatus of the chain
    policy_error: int  # CERT_CHAIN_POLICY_STATUS.dwError of the SSL policy, 0 = accepted

    @property
    def valid(self) -> bool:
        return self.chain_errors == 0 and self.policy_error == 0

    @property
    def name_constraint_violation(self) -> bool:
        return bool(self.chain_errors & NAME_CONSTRAINT_ERRORS)

    def __str__(self) -> str:
        return f"chain errors 0x{self.chain_errors:08X}, SSL policy 0x{self.policy_error:08X}"


def _winerror(what: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), what)


def _verify(dll: ctypes.WinDLL, engine: int, leaf_der: bytes, server_name: str) -> Verdict:
    leaf = dll.CertCreateCertificateContext(ENCODING, leaf_der, len(leaf_der))
    if not leaf:
        raise _winerror("CertCreateCertificateContext")
    chain = ctypes.POINTER(CERT_CHAIN_CONTEXT)()
    try:
        para = CERT_CHAIN_PARA(cbSize=ctypes.sizeof(CERT_CHAIN_PARA))
        flags = CERT_CHAIN_CACHE_ONLY_URL_RETRIEVAL | CERT_CHAIN_DISABLE_AUTH_ROOT_AUTO_UPDATE
        if not dll.CertGetCertificateChain(
            engine, leaf, None, None, ctypes.byref(para), flags, None, ctypes.byref(chain)
        ):
            raise _winerror("CertGetCertificateChain")
        ssl_para = SSL_EXTRA_CERT_CHAIN_POLICY_PARA(
            cbSize=ctypes.sizeof(SSL_EXTRA_CERT_CHAIN_POLICY_PARA),
            dwAuthType=AUTHTYPE_SERVER,
            pwszServerName=server_name,
        )
        policy_para = CERT_CHAIN_POLICY_PARA(
            cbSize=ctypes.sizeof(CERT_CHAIN_POLICY_PARA),
            pvExtraPolicyPara=ctypes.cast(ctypes.byref(ssl_para), ctypes.c_void_p),
        )
        status = CERT_CHAIN_POLICY_STATUS(cbSize=ctypes.sizeof(CERT_CHAIN_POLICY_STATUS))
        if not dll.CertVerifyCertificateChainPolicy(
            CERT_CHAIN_POLICY_SSL, chain, ctypes.byref(policy_para), ctypes.byref(status)
        ):
            raise _winerror("CertVerifyCertificateChainPolicy")
        return Verdict(chain.contents.TrustStatus.dwErrorStatus, status.dwError)
    finally:
        if chain:
            dll.CertFreeCertificateChain(chain)
        dll.CertFreeCertificateContext(leaf)


def verify_with_exclusive_root(leaf_der: bytes, root_der: bytes, server_name: str) -> Verdict:
    """Engine whose only trust anchor is ``root_der``, kept in memory: no store is touched."""
    dll = _crypt32()
    store = dll.CertOpenStore(CERT_STORE_PROV_MEMORY, 0, None, 0, None)
    if not store:
        raise _winerror("CertOpenStore")
    engine = wintypes.HANDLE()
    try:
        added = dll.CertAddEncodedCertificateToStore(
            store, ENCODING, root_der, len(root_der), CERT_STORE_ADD_ALWAYS, None
        )
        if not added:
            raise _winerror("CertAddEncodedCertificateToStore")
        config = CERT_CHAIN_ENGINE_CONFIG(cbSize=ctypes.sizeof(CERT_CHAIN_ENGINE_CONFIG), hExclusiveRoot=store)
        if not dll.CertCreateCertificateChainEngine(ctypes.byref(config), ctypes.byref(engine)):
            raise _winerror("CertCreateCertificateChainEngine")
        return _verify(dll, engine.value or 0, leaf_der, server_name)
    finally:
        if engine.value:
            dll.CertFreeCertificateChainEngine(engine)
        dll.CertCloseStore(store, 0)


def verify_with_machine_store(leaf_der: bytes, server_name: str) -> Verdict:
    """The machine's default engine: trust comes from the stores, as for WMP after setup."""
    return _verify(_crypt32(), HCCE_LOCAL_MACHINE, leaf_der, server_name)
