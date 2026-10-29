# Audit packet A001

Item I01: P = 4 cards, R = 3 slots; the tray is zone 0 (`dragzone`).

Rows are listed in the pipeline's replay order. `t` is seconds after the first row; `PV` is the export's Problem View value. Check payloads and grades are withheld: reconstruct the state at every CHECK row and at the end, then fill the reviewer sheet.

| row | t (s) | PV | kind | action |
|---|---|---|---|---|
| 1 | 0 | 1 | drop | `drag1 -> drop1` |
| 2 | 6 | 2 | drop | `drag2 -> drop2` |
| 3 | 11 | 3 | drop | `drag4 -> drop3` |
| 4 | 31 | 4 | drop | `drag3 -> drop2` |
| 5 | 35 | 5 | CHECK | (payload withheld) |
| 6 | 51 | 6 | drop | `drag2 -> drop1` |
| 7 | 55 | 7 | drop | `drag4 -> dragzone` |
| 8 | 85 | 8 | CHECK | (payload withheld) |


# Audit packet A002

Item I01: P = 4 cards, R = 3 slots; the tray is zone 0 (`dragzone`).

Rows are listed in the pipeline's replay order. `t` is seconds after the first row; `PV` is the export's Problem View value. Check payloads and grades are withheld: reconstruct the state at every CHECK row and at the end, then fill the reviewer sheet.

| row | t (s) | PV | kind | action |
|---|---|---|---|---|
| 1 | 0 | 1 | drop | `drag1 -> drop1` |
| 2 | 0 | 2 | drop | `drag1 -> drop1` |
| 3 | 10 | 3 | drop | `drag2 -> drop2` |
| 4 | 10 | 3 | CHECK | (payload withheld) |
| 5 | 15 | 4 | drop | `drag3 -> drop3` |
| 6 | 20 | 5 | drop | `drag3 -> dragzone` |
| 7 | 25 | 6 | CHECK | (payload withheld) |


# Audit packet A003

Item I01: P = 4 cards, R = 3 slots; the tray is zone 0 (`dragzone`).

Rows are listed in the pipeline's replay order. `t` is seconds after the first row; `PV` is the export's Problem View value. Check payloads and grades are withheld: reconstruct the state at every CHECK row and at the end, then fill the reviewer sheet.

| row | t (s) | PV | kind | action |
|---|---|---|---|---|
| 1 | 0 | 1 | CHECK | (payload withheld) |
| 2 | 10 | 2 | CHECK | (payload withheld) |
| 3 | 20 | 3 | drop | `garbage-action` (verbatim) |
| 4 | 30 | 4 | drop | `drag9 -> drop7` |


# Audit packet A004

Item I01: P = 4 cards, R = 3 slots; the tray is zone 0 (`dragzone`).

Rows are listed in the pipeline's replay order. `t` is seconds after the first row; `PV` is the export's Problem View value. Check payloads and grades are withheld: reconstruct the state at every CHECK row and at the end, then fill the reviewer sheet.

| row | t (s) | PV | kind | action |
|---|---|---|---|---|
| 1 | 0 | 1 | drop | `drag1 -> drop1` |
| 2 | 6 | 2 | drop | `drag2 -> drop1` |
| 3 | 11 | 3 | drop | `drag3 -> drop2` |
| 4 | 31 | 4 | drop | `drag4 -> drop3` |
| 5 | 35 | 5 | CHECK | (payload withheld) |
| 6 | 51 | 6 | drop | `drag4 -> dragzone` |
| 7 | 55 | 7 | CHECK | (payload withheld) |
| 8 | 85 | 8 | RESET |  |


# Audit packet A005

Item I01: P = 4 cards, R = 3 slots; the tray is zone 0 (`dragzone`).

Rows are listed in the pipeline's replay order. `t` is seconds after the first row; `PV` is the export's Problem View value. Check payloads and grades are withheld: reconstruct the state at every CHECK row and at the end, then fill the reviewer sheet.

| row | t (s) | PV | kind | action |
|---|---|---|---|---|
| 1 | 0 | 1 | drop | `drag1 -> drop1` |
| 2 | 5 | 2 | drop | `drag2 -> drop2` |
| 3 | 9 | 3 | drop | `drag1 -> drop1` |
| 4 | 12 | 4 | CHECK | (payload withheld) |
| 5 | 20 | 5 | RESET |  |
| 6 | 340 | 6 | drop | `drag3 -> drop3` |
| 7 | 345 | 7 | CHECK | (payload withheld) |

