# cdplayerweb

Webowy odtwarzacz płyt CD. Serwer działa na komputerze z napędem CD, a odtwarzacz
otwiera się w przeglądarce (domyślnie `http://<adres-komputera>:8080/`) na dowolnym
urządzeniu w sieci lokalnej. Muzyka gra w przeglądarce.

## Funkcje

- **Rozpoznawanie płyty.** Po włożeniu płyty serwer odczytuje jej spis utworów (TOC),
  liczy identyfikator płyty (MusicBrainz Disc ID) i pobiera z MusicBrainz tytuły
  utworów, wykonawcę, rok oraz okładkę z Cover Art Archive.
- **Odtwarzanie.** **Play** odtwarza płytę od pierwszego utworu, kolejne utwory grają
  automatycznie. Kliknięcie w utwór na liście odtwarza ten utwór. Dostępne są pauza,
  stop, poprzedni/następny utwór, przewijanie i głośność. Spacja włącza i wyłącza
  pauzę.
- **Zgrywanie na dysk.** Przycisk **Zgraj całą płytę na dysk (MP3)** zapisuje
  wszystkie utwory jako MP3 320 kb/s CBR z tagami ID3v2.3 i ID3v1 oraz wbudowaną
  okładką.
- **Odtwarzanie z dysku.** Gdy płyta jest zgrana, napęd służy tylko do odczytu spisu
  utworów i rozpoznania płyty. Muzyka gra z plików MP3, płyta nie jest czytana.
- **Ręczny opis.** Jeśli płyty nie ma w MusicBrainz albo nie ma internetu, opis
  (wykonawca, album, rok, tytuły) można uzupełnić ręcznie przed zgraniem.
- **Wysuwanie płyty** z poziomu strony.

## Wymagania

- Linux (dostęp do napędu przez ioctl `/dev/sr0`)
- Python 3.10 lub nowszy. Aplikacja korzysta tylko z biblioteki standardowej, nie
  trzeba niczego instalować przez pip.
- `ffmpeg` z obsługą `libcdio` (odczyt CD) i `libmp3lame` (kodowanie MP3)
- `eject`
- użytkownik, który uruchamia serwer, musi należeć do grupy `cdrom`
- dostęp do internetu, żeby pobrać opisy płyt (bez niego aplikacja działa,
  ale utwory nazywają się „Utwór 01”, „Utwór 02” itd.)

Instalacja na Ubuntu/Debianie:

```sh
sudo apt install python3 ffmpeg eject
sudo usermod -aG cdrom "$USER"   # potem wyloguj się i zaloguj ponownie
```

Sprawdzenie, czy ffmpeg ma wymagane moduły:

```sh
ffmpeg -hide_banner -devices  | grep libcdio
ffmpeg -hide_banner -encoders | grep libmp3lame
```

## Uruchomienie

```sh
cd ~/repos/cdplayerweb
python3 -m cdplayer
```

Następnie otwórz w przeglądarce `http://localhost:8080/` albo, z innego urządzenia
w sieci, `http://<adres-komputera>:8080/`. Serwer zatrzymasz klawiszami `Ctrl+C`.

### Opcje

Każdą opcję można podać w wierszu poleceń albo w zmiennej środowiskowej.

| opcja | zmienna środowiskowa | domyślnie | opis |
|---|---|---|---|
| `--port` | `CDPLAYER_PORT` | `8080` | port HTTP |
| `--host` | `CDPLAYER_HOST` | `0.0.0.0` | adres nasłuchu (`127.0.0.1` = tylko lokalnie) |
| `--device` | `CDPLAYER_DEVICE` | `/dev/cdrom` | napęd CD |
| `--music-dir` | `CDPLAYER_MUSIC_DIR` | `~/Music/CD` | katalog na zgrane płyty |
| `--data-dir` | `CDPLAYER_DATA_DIR` | `~/.local/share/cdplayerweb` | opisy płyt i okładki |
| `-v`, `--verbose` | | | szczegółowe logi |

Przykład:

```sh
python3 -m cdplayer --port 9000 --music-dir /srv/muzyka
```

### Jako usługa systemd (start razem z systemem)

Plik `cdplayerweb.service` zakłada, że projekt leży w `~/repos/cdplayerweb`. Jeśli
jest gdzie indziej, popraw `WorkingDirectory`.

```sh
mkdir -p ~/.config/systemd/user
cp cdplayerweb.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cdplayerweb
loginctl enable-linger "$USER"   # usługa działa także bez zalogowania
```

Przydatne polecenia:

```sh
systemctl --user status cdplayerweb     # stan
journalctl --user -u cdplayerweb -f     # logi na żywo
systemctl --user restart cdplayerweb    # restart, np. po aktualizacji kodu
```

Opcje do usługi można przekazać przez zmienne środowiskowe, na przykład dodając
w sekcji `[Service]` linię `Environment=CDPLAYER_PORT=9000`.

## Instrukcja obsługi

1. **Włóż płytę.** Po kilku sekundach pojawi się lista utworów. Przy pierwszym
   włożeniu danej płyty opis jest pobierany z MusicBrainz, co trwa chwilę
   (komunikat „Szukam opisu płyty…”).
2. **Słuchaj.** **▶** gra od początku płyty, kliknięcie w utwór gra ten utwór.
   Ikona przy utworze pokazuje, skąd gra muzyka:
   - 💿 prosto z płyty,
   - 💾 z pliku na dysku (utwór zgrany).
3. **Zgraj płytę.** Kliknij **Zgraj całą płytę na dysk (MP3)**. Pasek pokazuje
   postęp i aktualnie zgrywany utwór. W tym czasie można słuchać utworów już
   zgranych. **Przerwij zgrywanie** zatrzymuje proces, a przycisk **Dokończ
   zgrywanie płyty** zgrywa później brakujące utwory. Zgrana płyta ma na górze
   napis „zgrana na dysk”.
4. **Popraw opis (opcjonalnie).** Jeśli opis jest błędny lub płyty nie ma
   w MusicBrainz, kliknij **Edytuj opis** albo **Szukaj w MusicBrainz**, żeby
   pobrać opis ponownie. Zrób to *przed* zgraniem, bo opis trafia do tagów
   i nazw plików. Po zgraniu edycja jest zablokowana.
5. **Wysuń płytę** przyciskiem **Wysuń płytę**. Jest nieaktywny w trakcie
   zgrywania.

Odtwarzanie z płyty przerywa się po wyjęciu płyty. Odtwarzanie z dysku gra dalej
do końca bieżącego utworu.

## Gdzie są pliki

```
~/Music/CD/                                  (--music-dir)
└── Dire Straits/
    └── Brothers in Arms (1996)/
        ├── 01 - So Far Away.mp3
        ├── ...
        ├── 09 - Brothers in Arms.mp3
        ├── cover.jpg
        └── .cdplayer.json                   identyfikator płyty, opis, lista plików

~/.local/share/cdplayerweb/discs/            (--data-dir)
├── <disc-id>.json                           opis każdej włożonej płyty (cache)
└── <disc-id>.jpg                            okładka
```

- Płyty wielopłytowe dostają przyrostek `[CD n]` w nazwie katalogu.
- Na podstawie `.cdplayer.json` serwer przy starcie rozpoznaje zgrane albumy.
  Katalogi albumów można więc dowolnie przenosić i zmieniać im nazwy, byle
  w obrębie `--music-dir`. Nie zmieniaj nazw samych plików MP3.
- Żeby zgrać płytę od nowa, usuń jej katalog i zrestartuj serwer.

### Tagi w plikach MP3

`title`, `artist`, `album_artist`, `album`, `date` (rok), `track` (np. `3/9`),
`disc` (np. `1/1`), `MusicBrainz Album Id` oraz okładka (APIC, front cover).
Format: ID3v2.3 i ID3v1, czyli zgodny ze starszymi odtwarzaczami i Windows.

## Budowa aplikacji

```
cdplayer/
├── __main__.py   start: opcje, uruchomienie serwisów i serwera
├── cd.py         napęd: status, odczyt TOC (ioctl), Disc ID, wysuwanie
├── metadata.py   MusicBrainz i Cover Art Archive
├── library.py    katalog z MP3, cache opisów i okładek
├── service.py    DiscMonitor (obserwacja napędu), Ripper (zgrywanie)
├── server.py     serwer HTTP: strona i API JSON, strumieniowanie audio
└── static/       interfejs: index.html, style.css, app.js
```

- Odczyt audio i kodowanie MP3 wykonuje `ffmpeg` (wejście `libcdio`). Przy
  zgrywaniu używa trybu korekcji błędów `paranoia full`, przy odtwarzaniu
  z płyty go wyłącza, żeby dźwięk ruszał szybko.
- Utwór niezgrany jest strumieniowany do przeglądarki jako MP3 kodowane w locie.
  Przewijanie takiego utworu uruchamia strumień od nowa od wskazanej pozycji
  (parametr `?t=`).
- Pliki z dysku są serwowane z obsługą nagłówka `Range`, więc przewijanie jest
  natychmiastowe.
- Przeglądarka co 2 sekundy pobiera stan z `/api/state`.

### API

| metoda | ścieżka | opis |
|---|---|---|
| GET | `/api/state` | stan napędu, opis płyty z listą utworów, postęp zgrywania |
| GET | `/api/track/<disc-id>/<nr>[?t=sek]` | audio utworu (z pliku albo z płyty) |
| GET | `/api/cover/<disc-id>` | okładka JPEG |
| POST | `/api/rip` | rozpoczęcie lub dokończenie zgrywania |
| POST | `/api/rip/cancel` | przerwanie zgrywania |
| POST | `/api/eject` | wysunięcie płyty |
| POST | `/api/metadata/refresh` | ponowne pobranie opisu z MusicBrainz |
| PUT | `/api/metadata` | zapis opisu: `{"artist", "album", "year", "tracks": [{"number", "title"}]}` |

## Rozwiązywanie problemów

| objaw | przyczyna i rozwiązanie |
|---|---|
| „Nie można otworzyć napędu” | brak uprawnień: `groups` musi zawierać `cdrom`; zła ścieżka: sprawdź `--device` (`ls -l /dev/sr*`) |
| „To nie jest płyta audio” | w napędzie jest płyta z danymi lub DVD |
| tytuły „Utwór 01…” | płyty nie ma w MusicBrainz albo brak internetu; użyj **Edytuj opis** lub **Szukaj w MusicBrainz** |
| zgrywanie bardzo wolne | porysowana płyta: korekcja błędów wielokrotnie czyta trudne fragmenty, to normalne |
| „Błąd odczytu utworu N” | płyta uszkodzona w tym miejscu; wyczyść płytę i kliknij **Dokończ zgrywanie płyty** |
| przerywanie dźwięku z płyty w trakcie zgrywania | odtwarzanie i zgrywanie dzielą jeden napęd; słuchaj utworów już zgranych (💾) |
| strona nie otwiera się z innego komputera | zapora sieciowa, np. `sudo ufw allow 8080/tcp` |
| szczegóły błędów | uruchom z `-v` lub sprawdź `journalctl --user -u cdplayerweb` |

## Bezpieczeństwo

Aplikacja nie ma logowania. Każdy, kto ma dostęp do portu, może odtwarzać
muzykę, zgrywać i wysuwać płytę. Przeznaczona jest do sieci domowej. Żeby
ograniczyć ją do jednego komputera, uruchom z `--host 127.0.0.1`.

## Licencja

[MIT](LICENSE) © 2026 Piotr Oslowski
