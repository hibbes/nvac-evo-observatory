# 21.08.2026: zwei EVO-Wedges auf 7.2.0-gentoo-nvac-soak, beide PATH-2C

Erster Kernel mit lockdep UND disp=debug, der einen Wedge erlebt hat.
Beide vom Waechter nvac-s3-unwedge per S3-Zyklus geheilt, Sitzung blieb.

| | 14:17:40 (1026 s) | 18:56:45 (2019 s) |
|---|---|---|
| Ausloeser sichtbar | acquire SOR-1 (im Dauerlog, 14:18:35-Stapel) | acquire SOR-1 head-1 |
| SV1 | 0010 0140 | 0010 0140, head-1 1.0 |
| SV2 | NICHT vor dem Timeout | 0020 0150, head-1 2.0 / 2.2 / to SOR-1 |
| SV3 | nie | nie |
| Abbruch nach | SV1 | SV2.2 |
| core-ctrl | 8f0d001b | 8f0e001b |
| base1-ctrl | 8e06001b | 8e07001b |
| intr0 | 00000000 | 00000000 |
| Heilung | S3, danach SV1/SV2/SV3 vollstaendig (0540/0550) | S3, core zurueck auf 2d0b001b |

Beide: intr0 leer, also keine gerastete EVO-Ausnahme, Fetch-Park-Form.
Beide auf head-1 / SOR-1. Beide unmittelbar nach einem Output-Wechsel.

Der Heilungsweg des Waechters (S3-Zyklus) faehrt nachweislich die volle
Kette SV1 -> SV2 -> SV3, die vor dem Wedge abbrach. Das ist die sauberste
Messung von PATH-2C bisher, weil Vorher und Nachher im selben Log stehen.

Rohdaten:
  wedge-20260821T141752-watchdog.txt
  wedge-20260821T185658-watchdog.txt
  wedge-20260821T185658-sv-trace.txt   (279 Zeilen, 1985 bis 2040 s)
  Dauerlog /var/log/kernel/log-2026-08-21-14:55:35 (fuer 14:17)

Offen bleibt, was im Memory offen ist: WARUM der Supervisor nach SV1 oder
SV2.2 nicht weitergeht. Jetzt aber mit zwei Faellen, bei denen der
Ausloeser (acquire SOR-1) und die Abbruchstufe belegt sind.

Messfallen heute: 0x610204 ist base0, nicht base1 (base1 = 0x610208).
Zeitfilter ueber ISO-Zeit springt um den S3-Zyklus, ueber Kernel-Uptime
filtern.
