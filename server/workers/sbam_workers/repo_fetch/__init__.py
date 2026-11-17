"""The isolated repository fetcher (ADR 0028, decision 2; WG-7, #755).

A repository is untrusted input. It is cloned at one pinned commit, its files are copied out as data
(nothing runs), and the licence, the known vulnerabilities, the code patterns and malware are
checked
before anything is vendored. `scan.fetch_and_scan` is the whole run; each module here is one step.
"""
