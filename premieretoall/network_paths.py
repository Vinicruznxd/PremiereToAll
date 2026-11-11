"""Resolve UNC media paths using the current Windows user's network drives.

No drives are created and no credentials are requested. Host names are compared
by their resolved addresses only after the share and directory prefix match.
"""
from __future__ import annotations
import ctypes
import ipaddress
import ntpath
import os
import re
import socket
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class NetworkDrive:
    drive: str
    remote: str


def windows_network_drives():
    """Read active drive associations; never resurrect stale registry mappings."""
    if os.name != 'nt':
        return []
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    mpr = ctypes.WinDLL('mpr', use_last_error=True)
    logical_drives = kernel.GetLogicalDrives
    logical_drives.argtypes = []
    logical_drives.restype = wintypes.DWORD
    connection = mpr.WNetGetConnectionW
    connection.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    connection.restype = wintypes.DWORD
    mask = logical_drives()
    if not mask:
        raise ctypes.WinError(ctypes.get_last_error())
    result = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        drive = chr(ord('A') + index) + ':'
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        status = connection(drive, buffer, ctypes.byref(size))
        if status == 234 and 0 < size.value <= 32768:  # ERROR_MORE_DATA
            buffer = ctypes.create_unicode_buffer(size.value)
            status = connection(drive, buffer, ctypes.byref(size))
        if status == 0 and buffer.value:
            result.append(NetworkDrive(drive, buffer.value))
    return result


def _unc_parts(path):
    normalized = str(path).replace('\\', '/')
    if normalized[:8].casefold() == '//?/unc/':
        normalized = '//' + normalized[8:]
    if not normalized.startswith('//') or normalized.startswith(('//?/', '//./')):
        return None
    normalized = ntpath.normpath(normalized).replace('\\', '/')
    parts = normalized[2:].split('/')
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], tuple(p for p in parts[1:] if p)


def _host(host):
    host = host.strip('[]').rstrip('.').casefold()
    # Windows' spelling for literal IPv6 addresses in UNC server names.
    if host.endswith('.ipv6-literal.net'):
        host = host[:-17].replace('-', ':').replace('s', '%')
    return host


def _address(value):
    try:
        address = ipaddress.ip_address(value)
        return str(getattr(address, 'ipv4_mapped', None) or address)
    except ValueError:
        return None


def resolve_host_addresses(host, timeout=2.0):
    """Bound name-resolution delay; an unavailable name leaves its UNC intact."""
    literal = _address(host)
    if literal:
        return frozenset([literal])
    ready = threading.Event()
    result = set()

    def lookup():
        try:
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
                address = _address(item[4][0])
                if address:
                    result.add(address)
        except (OSError, UnicodeError):
            pass
        finally:
            ready.set()

    threading.Thread(target=lookup, daemon=True).start()
    return frozenset(result) if ready.wait(timeout) else frozenset()


class NetworkPathMapper:
    """One fresh mapping/address snapshot per conversion, with an audit trail."""
    def __init__(self, drives=(), resolver=resolve_host_addresses, discovery_error=''):
        self.drives = []
        self._roots = []
        for mapping in drives:
            parts = _unc_parts(mapping.remote)
            if not re.fullmatch(r'[A-Za-z]:', mapping.drive) or not parts:
                continue
            drive = NetworkDrive(mapping.drive.upper(), mapping.remote)
            self.drives.append(drive)
            self._roots.append((drive, _host(parts[0]), parts[1]))
        self.resolver = resolver
        self.discovery_error = discovery_error
        self._addresses = {}
        self._changes = {}
        self.unresolved_hosts = set()

    @classmethod
    def discover(cls):
        try:
            return cls(windows_network_drives())
        except OSError as exc:
            return cls(discovery_error=str(exc))

    def addresses(self, host):
        if host not in self._addresses:
            literal = _address(host)
            try:
                values = [literal] if literal else self.resolver(host)
                self._addresses[host] = frozenset(a for v in values if (a := _address(v)))
            except (OSError, UnicodeError):
                self._addresses[host] = frozenset()
            if not self._addresses[host]:
                self.unresolved_hosts.add(host)
        return self._addresses[host]

    def map_path(self, path):
        parts = _unc_parts(path)
        if not parts:
            return path  # Existing drive letters, local paths and URLs stay put.
        host, tail = _host(parts[0]), parts[1]
        folded = tuple(p.casefold() for p in tail)
        candidates = []
        for drive, remote_host, root in self._roots:
            prefix = tuple(p.casefold() for p in root)
            if folded[:len(prefix)] == prefix:
                candidates.append((drive, remote_host, root))
        # A drive mapped to a subfolder is the most specific match. Ties prefer
        # the exact server spelling, then a stable alphabetical drive letter.
        candidates.sort(key=lambda c: (-len(c[2]), c[1] != host, c[0].drive))
        for drive, remote_host, root in candidates:
            exact = host == remote_host
            if not exact and not (self.addresses(host) & self.addresses(remote_host)):
                continue
            mapped = drive.drive + '/' + '/'.join(tail[len(root):])
            self._changes.setdefault(str(path), {
                'original': str(path), 'mapped': mapped, 'drive': drive.drive,
                'remote': drive.remote, 'match': 'server_name' if exact else 'server_address',
            })
            return mapped
        return path

    def report(self):
        return {
            'mapped_drives': [{'drive': d.drive, 'remote': d.remote} for d in self.drives],
            'changed_paths_count': len(self._changes), 'changes': list(self._changes.values()),
            'unresolved_hosts': sorted(self.unresolved_hosts), 'discovery_error': self.discovery_error,
        }
