"""Offline trust contracts: no provider traffic or credential material."""
import hashlib
from pathlib import Path
import socket
import struct
import tempfile
import threading
import os
import ssl
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import supabase_management_database as p
from psycopg.conninfo import conninfo_to_dict


class ProviderCaTests(unittest.TestCase):
    def test_reviewed_asset_hash_parse_and_fingerprint(self):
        path = Path(p.resolve_provider_ssl_rootcert())
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7')
        der = ssl.PEM_cert_to_DER_cert(data.decode('ascii'))
        self.assertEqual(hashlib.sha256(der).hexdigest(),
                         '807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa')
        check = subprocess.run(['openssl', 'verify', '-CAfile', str(path), str(path)],
                               capture_output=True, check=False)
        self.assertEqual(check.returncode, 0)
        text = subprocess.check_output(['openssl', 'x509', '-in', str(path), '-noout', '-text']).decode()
        self.assertIn('CA:TRUE', text)
        self.assertIn('Certificate Sign, CRL Sign', text)
        dates = subprocess.check_output(['openssl', 'x509', '-in', str(path), '-noout', '-dates']).decode()
        self.assertEqual(dates, 'notBefore=Apr 28 10:56:53 2021 GMT\n'
                                'notAfter=Apr 26 10:56:53 2031 GMT\n')

    def test_missing_or_tampered_asset_fails_closed(self):
        for error in [FileNotFoundError(), PermissionError()]:
            with patch.object(Path, 'read_bytes', side_effect=error):
                with self.assertRaisesRegex(p.ProviderError, '^provider_ca_unavailable$'):
                    p.resolve_provider_ssl_rootcert()
        with patch.object(Path, 'read_bytes', return_value=b'tampered'):
            with self.assertRaisesRegex(p.ProviderError, '^provider_ca_integrity_failed$'):
                p.resolve_provider_ssl_rootcert()

    def test_all_production_dsn_use_fixed_provider_ca_and_reject_other_paths(self):
        args = dict(host='aws-0-eu-west-1.pooler.supabase.com', port=5432,
                    database='postgres', user='runtime.ref', password='synthetic', sslmode='verify-full')
        ca = p.resolve_provider_ssl_rootcert()
        self.assertEqual(conninfo_to_dict(p.build_psycopg_dsn(**args))['sslrootcert'], ca)
        for path in ['/arbitrary/ca.pem', p.resolve_system_ssl_rootcert(), 'system']:
            with self.assertRaises(p.ProviderError):
                p.build_psycopg_dsn(**args, sslrootcert=path)
        with self.assertRaises(p.ProviderError):
            p.build_psycopg_dsn(**dict(args, host='evil.example'))

    def test_runtime_custody_rebuild_and_old_trust_rejected(self):
        args = dict(host='aws-0-eu-west-1.pooler.supabase.com', port=5432,
                    database='postgres', user='runtime.ref', password='synthetic', sslmode='verify-full')
        stored = p.build_psycopg_dsn(**args)
        ca = p.resolve_provider_ssl_rootcert()
        expected = dict(expected_host=args['host'], expected_port=5432,
                        expected_database='postgres', expected_user='runtime.ref',
                        expected_sslmode='verify-full', expected_sslrootcert=ca)
        validated = p.validate_runtime_conninfo(stored, **expected)
        self.assertEqual(conninfo_to_dict(validated.conninfo)['sslrootcert'], ca)
        from psycopg.conninfo import make_conninfo
        for root in [p.resolve_system_ssl_rootcert(), '/arbitrary/ca.pem',
                     '/another-runner/app/lib/certs/supabase-prod-ca-2021.crt']:
            fields = conninfo_to_dict(stored)
            fields['sslrootcert'] = root
            with self.assertRaisesRegex(p.ProviderError, '^runtime_secret_sslrootcert_mismatch$'):
                p.validate_runtime_conninfo(make_conninfo(**fields), **expected)

    def test_node_fixed_asset_and_missing_tamper_fail_closed(self):
        script = r"""
const fs = require('fs');
const crypto = require('crypto');
const helper = require('./app/lib/supabaseProviderTrust');
const options = helper.supabaseProviderSslOptions();
if (!options.rejectUnauthorized || options.checkServerIdentity) process.exit(2);
const cert = new crypto.X509Certificate(options.ca);
if (!cert.ca || crypto.createHash('sha256').update(options.ca).digest('hex') !==
 '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7') process.exit(3);
const read = fs.readFileSync;
try {
 for (const mode of ['missing', 'tamper']) {
  fs.readFileSync = () => { if (mode === 'missing') throw Error('private'); return Buffer.from('bad'); };
  let code;
  try { helper.supabaseProviderSslOptions(); } catch (e) { code = e.message; }
  if (code !== (mode === 'missing' ? 'provider_ca_unavailable' : 'provider_ca_integrity_failed')) process.exit(4);
 }
} finally { fs.readFileSync = read; }
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b'')

    def test_real_loopback_driver_trust_and_hostname(self):
        import psycopg
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'), prefix='provider-ca-') as folder:
            cert, key = Path(folder) / 'cert.pem', Path(folder) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                '-keyout', str(key), '-out', str(cert)], check=True, timeout=20,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            # Test-only CA injection happens at the driver boundary, never via
            # any production parser/flag/env override or shipped trust asset.
            for driver in ['python', 'node']:
                for trust, hostname, expected in [
                    ('fixture', 'localhost', True), ('system', 'localhost', False),
                    ('provider', 'localhost', False), ('fixture', 'wrong.invalid', False)]:
                    with self.subTest(driver=driver, trust=trust, hostname=hostname):
                        seen = {'tls': False, 'startup': False}
                        errors = []
                        with socket.socket() as listener:
                            listener.bind(('127.0.0.1', 0))
                            listener.listen(1)
                            listener.settimeout(5)
                            port = listener.getsockname()[1]
                            def serve():
                                try:
                                    raw, _ = listener.accept()
                                    with raw:
                                        raw.settimeout(5)
                                        request = b''
                                        while len(request) < 8:
                                            chunk = raw.recv(8 - len(request))
                                            if not chunk:
                                                raise AssertionError('incomplete request')
                                            request += chunk
                                        if request != struct.pack('!II', 8, 80877103):
                                            raise AssertionError('wrong request')
                                        raw.sendall(b'S')
                                        with context.wrap_socket(raw, server_side=True) as client:
                                            seen['tls'] = True
                                            seen['startup'] = bool(client.recv(8192))
                                            if driver == 'python' and seen['startup']:
                                                payload = b'SFATAL\0C28000\0Mfixture stops before authentication\0\0'
                                                client.sendall(b'E' + struct.pack('!I', len(payload) + 4) + payload)
                                except (ssl.SSLError, ConnectionResetError):
                                    pass  # A client rejecting a CA/hostname aborts TLS.
                                except Exception as exc:
                                    errors.append(type(exc).__name__)
                            thread = threading.Thread(target=serve, daemon=True)
                            thread.start()
                            try:
                                if driver == 'python':
                                    root = {'fixture': str(cert), 'system': p.resolve_system_ssl_rootcert(),
                                            'provider': p.resolve_provider_ssl_rootcert()}[trust]
                                    with self.assertRaises(psycopg.Error) as caught:
                                        psycopg.connect(host=hostname, hostaddr='127.0.0.1', port=port,
                                            dbname='fixture', user='fixture', password='synthetic',
                                            sslmode='verify-full', sslrootcert=root, connect_timeout=3)
                                    if expected:
                                        self.assertIn('fixture stops before authentication', str(caught.exception))
                                    elif hostname == 'wrong.invalid':
                                        self.assertIn('does not match host name', str(caught.exception))
                                    else:
                                        self.assertIn('certificate verify failed', str(caught.exception))
                                else:
                                    script = r"""
const net = require('net'), tls = require('tls'), fs = require('fs');
const mode = process.argv[1], hostname = process.argv[2];
const opts = mode === 'provider' ? require('./app/lib/supabaseProviderTrust').supabaseProviderSslOptions() :
 {rejectUnauthorized: true, ...(mode === 'fixture' ? {ca: fs.readFileSync(process.argv[3])} : {})};
const raw = net.connect({host: '127.0.0.1', port: Number(process.argv[4])},
 () => raw.write(Buffer.from([0,0,0,8,4,210,22,47])));
raw.setTimeout(3000, () => process.exit(3));
raw.once('data', data => {
 if (data.toString() !== 'S') process.exit(4);
 let observed = false;
 const report = status => { if (!observed) { observed = true; process.stdout.write(status); } };
 const client = tls.connect({...opts, socket: raw, servername: hostname}, () => {
  report('trusted'); client.end('synthetic startup');
 });
 // TLS trust is decided at secureConnect; later peer teardown is separate.
 client.on('error', () => report('rejected'));
});
raw.on('error', () => process.exit(5));
"""
                                    run = subprocess.run(['node', '-e', script, trust, hostname, str(cert), str(port)],
                                                         cwd=ROOT, capture_output=True, timeout=8)
                                    self.assertEqual(run.returncode, 0, run.stderr.decode())
                                    self.assertEqual(run.stdout, b'trusted' if expected else b'rejected')
                            finally:
                                thread.join(7)
                                listener.close()
                                thread.join(2)
                            self.assertFalse(thread.is_alive())
                            self.assertEqual(errors, [])
                            self.assertEqual(seen['startup'], expected)
