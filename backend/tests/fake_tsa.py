"""A fake RFC 3161 timestamp authority for tests: real tokens, signed by a throwaway certificate.

mode: "ok" | "refuse" | "garbage" | "down" | "tamper" (content changed after signing) | "wrong_digest"
"""
from __future__ import annotations

import datetime as dt
import hashlib

from asn1crypto import algos, cms, core, tsp, x509 as ax509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


class FakeTSA:
    def __init__(self, name: str = "Benchtop Test TSA"):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Tests")])
        now = dt.datetime.now(dt.timezone.utc)
        self.cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(self.key.public_key())
                     .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                     .not_valid_after(now + dt.timedelta(days=365))
                     .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
                     .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                     .sign(self.key, hashes.SHA256()))
        self.cert_der = self.cert.public_bytes(serialization.Encoding.DER)
        self.acert = ax509.Certificate.load(self.cert_der)
        self.mode = "ok"
        self.serial = 1000
        self.requests = 0

    @property
    def cert_pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)

    def __call__(self, url: str, body: bytes) -> bytes:
        self.requests += 1
        if self.mode == "down":
            raise OSError("connection refused")
        if self.mode == "garbage":
            return b"<html>Service unavailable</html>"
        req = tsp.TimeStampReq.load(body)
        if self.mode == "refuse":
            status = tsp.PKIStatusInfo({"status": "rejection", "status_string": ["policy not supported"]}).dump()
            return b"\x30" + bytes([len(status)]) + status  # a TimeStampResp holding only the status, as real refusals do
        imprint = req["message_imprint"]
        if self.mode == "wrong_digest":
            imprint = tsp.MessageImprint({"hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}), "hashed_message": b"\x00" * 32})
        self.serial += 1
        tst = tsp.TSTInfo({"version": "v1", "policy": "1.2.3.4.1", "message_imprint": imprint, "serial_number": self.serial,
                           "gen_time": dt.datetime.now(dt.timezone.utc).replace(microsecond=0), "nonce": req["nonce"].native})
        tst_der = tst.dump()
        ess = tsp.SigningCertificateV2({"certs": [tsp.ESSCertIDv2({"hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                                                                   "cert_hash": hashlib.sha256(self.cert_der).digest()})]})
        attrs = cms.CMSAttributes([
            cms.CMSAttribute({"type": "content_type", "values": ["tst_info"]}),
            cms.CMSAttribute({"type": "message_digest", "values": [hashlib.sha256(tst_der).digest()]}),
            cms.CMSAttribute({"type": "signing_certificate_v2", "values": [ess]}),
        ])
        signature = self.key.sign(attrs.dump(), padding.PKCS1v15(), hashes.SHA256())
        if self.mode == "tamper":  # the content changes after the TSA signed it
            tst = tsp.TSTInfo({**tst.native, "gen_time": dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc),
                               "message_imprint": req["message_imprint"].native})
            tst_der = tst.dump()
        si = cms.SignerInfo({
            "version": "v1",
            "sid": cms.SignerIdentifier({"issuer_and_serial_number": cms.IssuerAndSerialNumber(
                {"issuer": self.acert.issuer, "serial_number": self.acert.serial_number})}),
            "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": attrs,
            "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
            "signature": signature,
        })
        sd = cms.SignedData({
            "version": "v3", "digest_algorithms": [algos.DigestAlgorithm({"algorithm": "sha256"})],
            "encap_content_info": {"content_type": "tst_info", "content": core.ParsableOctetString(tst_der)},
            "certificates": [self.acert], "signer_infos": [si],
        })
        token = cms.ContentInfo({"content_type": "signed_data", "content": sd})
        return tsp.TimeStampResp({"status": {"status": "granted"}, "time_stamp_token": token}).dump()
