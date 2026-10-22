<!--
  SAMPLE REPORT — for documentation only.
  This is a real report produced by the `pentest-report` skill (report_gen.py) during an
  authorized, operator-owned fingerprint engagement, then REDACTED for publication: the target
  IP is replaced with the RFC 5737 documentation address 192.0.2.1, and the device serial number
  and hostname are redacted. The report body below is the verbatim output of report_gen.py
  (which emits English). See docs/example-findings.jsonl for the matching findings store.
-->

# Pentest report — engagement `eng-linksys-mr7350-fingerprint`

- **Generated:** 2026-10-16T18:24:20Z
- **Total recorded findings:** 6

## Executive summary

The engagement confirmed **6** finding(s) with reproducible evidence. The technical detail, evidence and remediation for each are below. Items marked as hypothesis or partial require further verification and do not constitute confirmed exploitation.

## Summary by status

| Status | Count |
|---|---|
| Confirmed | 6 |
| Partial | 0 |
| Hypothesis | 0 |
| Discarded | 0 |

## Methodology

The engagement was driven by an **OODA** loop (Observe-Orient-Decide-Act), orienting by **MITRE ATT&CK** tactics and techniques and drawing on **PTES** and **OWASP** practices according to the surface (network, cloud, web). Each finding requires reproducible evidence of the executed command; there is no confirmed finding without proof.

## Findings

### Confirmed (6)

### F1. Device identified via JNAP GetDeviceInfo: Linksys MR7350 Dual-Band Mesh WiFi 6 Router, hardware v1, firmware 1.1.7.216943 (2026-08-20)

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1592 |
| Target | 192.0.2.1:80,443/tcp (POST /JNAP/) |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
curl -sS --max-time 15 -X POST http://192.0.2.1/JNAP/ -H 'X-JNAP-Action: http://linksys.com/jnap/core/GetDeviceInfo' -H 'Content-Type: application/json' -d '{}'
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
{"result": "OK", "output": {"manufacturer": "Linksys", "modelNumber": "MR7350", "hardwareVersion": "1", "description": "Linksys Dual-Band Mesh WiFi 6 Router", "serialNumber": "REDACTED", "firmwareVersion": "1.1.7.216943", "firmwareDate": "2026-08-20T23:58:42Z", "services": ["http://linksys.com/jnap/core/Core", ... ~100 JNAP service URIs ...]}} [HTTP 200]
````

### F2. Service/version inventory: dnsmasq 2.85 (53), lighttpd 1.4.39 (80/443/8080), SMB (139/445), MQTT (1883), UPnP Portable SDK 1.6.19 (49152); OS Linux 4.4.60

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1595 |
| Target | 192.0.2.1 (ports 53,80,139,443,445,1883,8080,49152) |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
nmap -Pn -sV -T4 -p 53,80,139,443,445,1883,8080,49152 192.0.2.1
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
Nmap scan report for Router-EXAMPLE (192.0.2.1)
Host is up (0.0025s latency).
PORT      STATE SERVICE      VERSION
53/tcp    open  domain       dnsmasq 2.85
80/tcp    open  http         lighttpd 1.4.39
139/tcp   open  netbios-ssn?
443/tcp   open  ssl/http     lighttpd 1.4.39
445/tcp   open  microsoft-ds
1883/tcp  open  mqtt
8080/tcp  open  http         lighttpd 1.4.39
49152/tcp open  upnp         Portable SDK for UPnP devices 1.6.19 (Linux 4.4.60; UPnP 1.0)
Service Info: OS: Linux; CPE: cpe:/o:linux:linux_kernel:4.4.60
SMB probe: Workgroup ROUTER-EXAMPLE
Nmap done: 1 IP address (1 host up) scanned in 129.78 seconds
````

### F3. Web admin interface served by lighttpd/1.4.39 on 80 and 443 (HTTP 200, X-Frame-Options: SAMEORIGIN, no-store); port 8080 also lighttpd/1.4.39 but returns 403 Forbidden

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1592 |
| Target | 192.0.2.1:80,443,8080/tcp |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
curl -sS -I --max-time 15 http://192.0.2.1/ ; curl -sSk -I --max-time 15 https://192.0.2.1/ ; curl -sS -I --max-time 15 http://192.0.2.1:8080/
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
=== HTTP 80 ===
HTTP/1.1 200 OK
X-Frame-Options: SAMEORIGIN
Content-Type: text/html; charset=UTF-8
Cache-Control: no-store, no-cache, must-revalidate, post-check=0, pre-check=0
Pragma: no-cache
CONTENT-LANGUAGE: en
Server: lighttpd/1.4.39
=== HTTPS 443 ===
HTTP/1.1 200 OK
(same headers)
Server: lighttpd/1.4.39
=== HTTP 8080 ===
HTTP/1.1 403 Forbidden
Content-Type: text/html
Content-Length: 345
Server: lighttpd/1.4.39
````

### F4. Admin UI is the Linksys RAINIER web stack, UI build 1.0.99.216943 ('Linksys Smart Wi-Fi', RAINIER.browser JS framework, assets under /ui/1.0.99.216943/)

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1592 |
| Target | 192.0.2.1:80/tcp (GET /) |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
curl -sS --max-time 15 http://192.0.2.1/ | head -c 3000
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
<title>Linksys Smart Wi-Fi</title>
<meta name="description" content="Included with your Linksys Smart Wi-Fi Router, ...">
<noscript><meta http-equiv="REFRESH" content="0;url=/ui/1.0.99.216943/dynamic/script-disabled.html"></noscript>
<script ... src="/ui/1.0.99.216943/static/js/shared/shared-util.js"></script>
<script ... src="/ui/1.0.99.216943/static/js/any_device/jnap-simple.js"></script>
var browser = RAINIER.browser, ...
window.location.replace('/ui/1.0.99.216943/dynamic/login.html' + urlSuffix);
RAINIER.shared.util.setCookie('visited-index', true, null, '/');
````

### F5. HTTPS on 443 negotiates TLSv1.2 (ECDHE-RSA-AES256-GCM-SHA384, secp256r1) with a self-signed certificate CN=linksyssmartwifi.com issued by O=Belkin International, Inc.

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1592 |
| Target | 192.0.2.1:443/tcp |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
curl -sSkv --max-time 15 https://192.0.2.1/ -o /dev/null 2>&1 | grep -Ei 'SSL connection|subject:|issuer:|TLS|cipher'
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
* SSL connection using TLSv1.2 / ECDHE-RSA-AES256-GCM-SHA384 / secp256r1 / rsaEncryption
*   subject: C=US; ST=California; O="Belkin International, Inc."; OU="Belkin International, Inc."; CN=linksyssmartwifi.com
*   issuer: C=US; ST=California; O="Belkin International, Inc."; OU="Belkin International, Inc."; CN=linksyssmartwifi.com
````

### F6. JNAP management API exposed without authentication: unauthenticated POST /JNAP/ discloses full device info (model, serial, firmware) and the complete ~100-URI JNAP service surface on both 80 and 443

| Field | Value |
|---|---|
| Status | Confirmed |
| OODA phase | observe |
| ATT&CK technique | T1592 |
| Target | 192.0.2.1:80,443/tcp (POST /JNAP/) |
| Confidence | high |
| CVSS 3.1 | — (not scored) |
| CWE | — |

**Evidence:**

**Command:**
````
curl -sS --max-time 15 -X POST http://192.0.2.1/JNAP/ -H 'X-JNAP-Action: http://linksys.com/jnap/core/GetDeviceInfo' -H 'Content-Type: application/json' -d '{}'  (no credentials, no session, no auth headers sent)
````
**Return code:** `0`
**Output (verbatim, untrusted data):**
````
{"result": "OK", "output": {"manufacturer": "Linksys", "modelNumber": "MR7350", "serialNumber": "REDACTED", "firmwareVersion": "1.1.7.216943", "services": [ ... ]}} [HTTP 200] -- identical result over HTTPS 443. Note: the short action form '/jnap/core/GetDeviceInfo' returns {"result": "_ErrorUnknownAction"}; the canonical form 'http://linksys.com/jnap/core/GetDeviceInfo' returns OK.
````

## Scope and limitations

This report reflects only the findings recorded during the engagement against operator-authorized targets. Responsibility for the scope rests with the operator. The included evidence is raw tool output, presented verbatim.

---

> **Note.** This is a redacted sample of a real `pentest-report` artifact produced by Vulnerator AI
> (report body generated verbatim by `report_gen.py`). The target address is the RFC 5737
> documentation range (192.0.2.1); the device serial number and hostname were redacted. Product and
> version strings are left intact as they identify the product, not a specific unit.
