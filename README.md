# Familieplanner

Overblik over aftaler, skema og lektier for Andreas, Monica, Hugo, Carla og Leo – hentet fra Aula og Google Kalender.

```
familieplanner/
  fetch_family.py        henter data og skriver web/family.json
  config.toml            din opsætning (lav den ud fra config.example.toml)
  secrets/               Aula-tokens (oprettes automatisk)
  web/index.html         selve appen
  web/family.json        data (skrives af scriptet)
```

## Kom i gang

Aula-klienten kræver **Python 3.14**. Nemmest med [uv](https://docs.astral.sh/uv/):

```bash
cp config.example.toml config.toml     # udfyld MitID-brugernavn og iCal-adresse

# 1) Test Google-delen først
uv run --python 3.14 --with-requirements requirements.txt fetch_family.py --no-aula

# 2) Første Aula-login: scan QR-koden i terminalen med MitID-appen
uv run --python 3.14 --with-requirements requirements.txt fetch_family.py

# 3) Kør løbende (hvert 15. min)
uv run --python 3.14 --with-requirements requirements.txt fetch_family.py --watch 900

# Server appen på hjemmenetværket
python3 -m http.server 8080 -d web      # åbn http://<maskinens-ip>:8080
```

## Sådan tildeles aftaler til personer

- **Aula**: automatisk. Børnene matches på fornavn (`aula_name`), og det, der hører til din egen Aula-profil, lander hos Andreas.
- **Google**: ud fra navne i titlen. "Hugo fodbold" → Hugo. "Carla + Leo svømning med mor" → Carla, Leo og Monica. Uden navn → hele familien (kan ændres med `default_people`). Aliasserne styres i `config.toml`.

Skemaets enkelte timer slås sammen til én "Skole"-blok per barn per dag (timerne står i detaljerne, og vikarer markeres). Slå det fra med `collapse_lessons = false`.

## Hvad hentes fra Aula

- Kalender og skema (skematimer samles til én blok per dag)
- Lektier fra Min Uddannelse (hvis skolen bruger det)
- Ugeplan fra Meebook (denne og næste uge)
- Opslag med billeder, hentet per barn så de kan filtreres (billeder gemmes i `web/media`)
- **Hele beskedhistorikken** (op til 500 tråde, 50 beskeder i hver), med ulæst-markering og evt. billeder. Første kørsel tager lidt længere tid, fordi alle tråde skal hentes; derefter hentes kun tråde, der er nye eller ændret. Beskeder ældre end 60 dage kan søges og læses, men giver ikke opgaver eller aftaler i kalenderen (`messages_analyse_days`). Billeder hentes kun fra de seneste 90 dage (`message_images_days`).
- Galleri-albums (de nyeste 8, op til 12 billeder hver). Børn, der er tagget på billederne, vises på kortet

Billeder tjekkes på indholdet, så fejlsider aldrig gemmes som billeder. iPhone-billeder (HEIC) kan ikke vises i Chrome/Edge – installér `pillow-heif` for at få dem konverteret til JPG, ellers bruges Aulas miniaturebillede:
`uv run --python 3.14 --with-requirements requirements.txt --with pillow-heif fetch_family.py`

Hver del kan slås fra i `config.toml`. Børnenes ikoner (`icon`) sættes også der. Fejler én del, genbruges de forrige data for netop den del.

## Lektie-genkendelse (homework.py)

Meebook-ugeplanen analyseres sætning for sætning og hvert punkt får en kategori: lektie, husk, praktisk info eller undervisning. Lektier og husk-ting lander i "Husk og lektier" med den rigtige dag ("på mandag", "Afleveres uge 43", "hver dag").

- `homework_min_confidence` i `config.toml` styrer hvor forsigtig den er ("høj", "middel", "lav").
- `class = "6.B"` under et barn sorterer parallelklassens punkter fra.
- Test ændringer uden Aula: `python homework_test.py weekplan.json`

## Skema

"I dag" viser børnenes skema for dagen øverst. Lektionerne kommer fra Aulas skemabegivenheder, og hvis skolen i stedet skriver skemaet som tekst i en aftale ("08.00-08.45 Dansk"), hentes aftalens beskrivelse og skemaet læses ud af den. Hver lektion vises med start og slut (fx 08.00–08.45). Nuværende lektion markeres, overståede får et ✓, og vikarer vises.

**Tider.** Overalt i appen vises en aftale med både start- og sluttid, når den har en sluttid. Mangler sluttiden (fx en Google-aftale uden sluttid, eller en aktivitet i en besked, hvor kun starttidspunktet er nævnt), vises kun starttidspunktet med "sluttid ukendt" – appen finder ikke selv på en sluttid. En lektion uden sluttid varer 45 minutter (`lesson_minutes` under `[aula]`), og det samme gælder forslag om test og prøver eller aktiviteter "i 3. lektion", hvor kun starttidspunktet står.

Skemaet gøres læsevenligt (`schedule.py`): fagene skrives ud (DAN → Dansk), lærere forkortes ("Anni C."), vikarer vises med navn, og timer, der følger klassen og ikke barnet, udelades (INK = støttelærer til en anden elev, PS = klassepædagogen). Hvad der skjules, og hvad forkortelser betyder, styres i `config.toml` (`hidden_subjects`, `secondary_subjects`, `[subjects]`). Har en lektion en note fra skolen (fx til vikaren), hentes den og vises under timen.

Fejlsøgning: `fetch_family.py --dump-aula` gemmer rå kalenderdata for ±3 dage i `aula_dump.json`.

## Beskeder

Fanen "Beskeder" virker som en mailklient: kompakt liste til venstre, den valgte besked i fuld bredde til højre med hele tråden (nyeste først, ældre foldet sammen). Søg, filtrér på ulæste, "Skal gøres" eller "Med datoer", og brug ↑/↓ til at bladre. På mobil åbner beskeden i fuld skærm. Beskeder, du har åbnet, markeres som set på den enhed (de forbliver ulæste i Aula).

## Feed

Fanen "Feed" viser opslag og billeder fra Aula, nyeste først, og kan filtreres på børn og på **Alt / Opslag / Billeder**. Søgefeltet øverst filtrerer, mens du skriver: det søger i titel, tekst og afsender, alle ord skal findes (i vilkårlig rækkefølge), og træf markeres. Lange opslag vises i fuld længde under søgning. `Esc` rydder søgningen. Der søges kun i det, appen har hentet (`posts_limit` opslag pr. barn og de `gallery_albums` nyeste albums); ældre opslag findes i Aula.

## Besked-analyse (messages.py)

Hver besked får en kategori (skal gøres, arrangement, hilsen, info eller privat samtale) og det rigtige barn:

- Klasse/årgang i teksten ("forældre i 6B", "3. årgang") slår Aulas grove tilknytning. Kræver `class` eller `grade` under børnene i `config.toml` – husk at opdatere dem i august.
- Private samtaler mellem jer og skolen skjules bag "Vis besked" (`hide_private`), og der trækkes ikke opgaver ud af dem.
- Handlinger ("Skriv jer på senest fredag d.25.9") og medbring-lister lander i "Husk og lektier". Arrangementer med dato lander i ugetavlen med stiplet kant.
- Test uden Aula: `python messages_test.py aula_feed.json`

## Overblik (briefing.py)

Øverst i "I dag" og "Ugen" står et overblik. **Med AI** er det en varm, kronologisk fortælling til jer forældre – fra morgen til aften (ugen: dag for dag) – i nogle få korte afsnit, ca. 150 ord: hvad der skal med ud ad døren, afvigelser i skoledagen, afhentning, fritid og aftaler, og vejret, hvor det betyder noget. Det normale skoleskema nævnes ikke – det står allerede i appen. Hvem der henter/bringer nævnes kun, hvis det står i en aftale eller i `familie_regler.md`. Kioskskærmen viser fortællingen under "Dagen".

**Uden AI** laver appens egne regler en liste: *Vejr*, *Husk*, *Skal gøres*, *Særligt* og *Kommende frister*, hvert punkt med barnets ikon og kilde.

**`mode = "ai"`: sprogmodellen skriver overblikket.** Appens egne regler finder fakta (datoer, frister, hvem); sprogmodellen formulerer og prioriterer. Standard er Google Gemini på det gratis niveau. Hvis sprogmodellen ikke kan bruges (ingen nøgle, kvoten er brugt, Google er nede, svaret er ugyldigt), sker der dette:

- Findes der et AI-overblik for samme dag/uge, vises det stadig, og et banner øverst siger *"AI ikke tilgængelig – overblikket er fra kl. … og er måske ikke opdateret"*.
- Ellers laver appens egne regler overblikket, og banneret siger *"AI ikke tilgængelig – overblikket er lavet af appens egne regler"*.
- Banneret vises også på kioskskærmen. Den tekniske grund står i serverens log og i `/api/status` (kun efter login).

**`mode = "offline"`:** kun egne regler, intet forlader maskinen. **`mode = "off"`:** intet overblik.

### Sæt Gemini op (gratis niveau)

1. Gå til aistudio.google.com med en privat Google-konto og lav en API-nøgle i et projekt. **Projektets fakturering (billing) skal forblive slået fra** – så kan det gratis niveau aldrig koste penge. Begræns nøglen til "Generative Language API".
2. Skriv nøglen i `.env` på én linje, uden mellemrum og uden anførselstegn: `GEMINI_API_KEY=din-nøgle`.
3. Find jeres grænser i AI Studio (requests per minute = RPM, requests per day = RPD) for modellen, og sæt `[ai] rpm` til højst RPM og `[ai] daily_cap` lidt under RPD i `config.toml`. Uden tal bruges 5 i minuttet og 100 om dagen – appen bruger normalt 15–30 om dagen.
4. Sæt `[assistant] mode = "ai"` i `config.toml`, og genstart serveren.
5. Prøv for alvor: `python server.py --selftest --no-notify`. Linjen *Familieassistent* skal stå med ✔. Står der ✖, forklarer den, hvad der er galt.
6. Se præcis hvad der sendes: `python briefing.py --dry-run`.

**Vilkår, I selv har taget stilling til:** På Googles gratis niveau må Google bruge det, der sendes, til at forbedre sine tjenester, og vilkårene er skrevet til voksne brugere, mens overblikket også vises på kioskskærmen for børnene. Til fortællingen sendes: alle aftaler fra Google Kalender og Aula (titel, tid, sted og note), opgaver, ugeplaner, afvigelser i skemaet (vikar, noter), skolens nyheder og ikke-private beskeder fra de seneste dage, og vejret. Private samtaler sendes aldrig, og telefonnumre, mailadresser og CPR-numre fjernes først – men sted og note fra kalenderen kan stadig indeholde fx en adresse eller en lægetid. `familie_regler.md` sendes som den er – skriv ikke telefonnumre eller CPR-numre i den.

**Skift udbyder** i `config.toml` under `[ai]`: `provider = "claude"`, `model = "…"` og `api_key_env = "ANTHROPIC_API_KEY"` (nøgle fra platform.claude.com i `.env`). Ældre opsætning med `mode = "claude"` virker stadig.

**Budget:** Svar gemmes i `web/ai_cache.json`, så uændrede data aldrig koster en ny forespørgsel, og forbruget tælles i `web/ai_usage.json`. Dagen tælles i Stillehavstid som hos Google, så budgettet nulstilles ved midnat i Californien (normalt kl. 9 dansk tid). Efter en fejl holder appen pause, før den prøver igen (længere for hver fejl i træk). Begge filer indeholder familiens data og må aldrig i git.

### Kalenderforslag fra AI

Med `mode = "ai"` læser sprogmodellen nye Aula-beskeder (aldrig private), opslag og ugeplaner og finder aftaler, der hører hjemme i kalenderen – fx *"Vi tager i skoven næste torsdag, mødetid 8.15 ved den røde port"*. Der er ingen ny skærm: knappen **Føj til kalender** vises kun på de punkter, hvor der er fundet noget, og formularen er udfyldt med titel, dato, tid, sted og barn. I retter, hvis noget er forkert, og trykker Gem. Intet skrives i kalenderen uden jeres klik. Siger en besked, at en aftale, I har oprettet fra appen, er aflyst eller flyttet, vises det på beskeden.

Alt, hvad AI'en foreslår, kontrolleres mod punktets egen tekst: datoen skal stå der (eller ugedagen, op til to uger efter beskeden er skrevet), klokkeslæt og sted skal stå der, og en aflysning skal passe til præcis én aftale, appen har oprettet. Ellers droppes forslaget. Hvert punkt vurderes kun én gang. Første gang tjekkes de seneste 14 dage; derefter kun nye og ændrede punkter, højst 3 forespørgsler pr. hentning, og 30 af dagens forespørgsler holdes altid tilbage til overblikket. Uden AI – eller når kvoten er brugt – finder appens egne regler forslagene som før. Slå det fra med `[calendar_ai] enabled = false`.

### Vejr (DMI)

Overblikket får et afsnit **Vejr** med kort prognose og praktiske råd ("8–11°, regn om eftermiddagen – regntøj og gummistøvler"). Under datoen øverst i appen står dagens vejr som ét ikon med temperatur – tryk på det for at se resten af dagen time for time (ikon, temperatur og regn). Efter kl. 19 viser det i morgen. Kioskskærmen viser vejrlinjen under datoen; tryk på den for at folde timerne ud. På kiosken lukker timerne sig selv efter 30 sekunder. Vejret kommer fra DMI's åbne data (vejrmodellen HARMONIE) – ingen nøgle, ingen konto. HARMONIE rækker kun et par døgn frem, så ugeoverblikket har kun vejr for de dage, DMI dækker.

**Sæt hjemmet én gang:** Åbn appen i browseren **på pc'en, der kører den** (http://localhost:8080), tryk på **Vejr: hjem** og **Brug min placering som hjem**. Browseren spørger om lov. Siger den nej, så slå placering til i Windows: *Indstillinger → Privatliv og sikkerhed → Placering*. Knappen virker kun på selve pc'en (eller over https), fordi browsere kun udleverer placeringen til sikre sider. En stationær pc finder sin placering via wifi eller internetadressen, så den kan være et par kilometer ved siden af – det betyder intet for vejret. Tryk på "Se på kort" for at tjekke den. Flytter I, så tryk igen.

**Privatliv:** Placeringen afrundes til ca. 1 km og gemmes kun i `web/home_location.json` (aldrig i git, aldrig i `family.json`). Kun den afrundede placering sendes til DMI, højst én gang i timen. Er DMI nede, bruges en prognose op til 6 timer gammel; ellers er overblikket bare uden vejr. Tjek med selvtesten: linjerne *Vejr: hjem* og *Vejr (DMI)*.

**Er vejret med?** Serverens log skriver én linje pr. hentning, fx `Vejr: 2 dage fra DMI (prognose hentet kl. 08:12)` – eller hvorfor ikke: `Vejr: hjemmets placering er ikke sat …`, `Vejr: ingen brugbar prognose fra DMI …` eller `Vejr: slået fra i config.toml`. Mangler hjemmet, står knappen øverst som **Vejr: hjem er ikke sat** med rød skrift.

## Udseende

- Titlen er "Familieplan". Ikonet i browserfanen (en proppet kalender i børnenes farver) ligger som `web/favicon.svg` med PNG-udgaver (`favicon-32.png`, `apple-touch-icon.png` til hjemmeskærmen på iPad/iPhone). Læg dine egne filer med samme navne i `web/` for at skifte det.
- "Dagens aftaler" viser ikke skoledagens blokke fra Aula, fordi skemaet allerede står øverst. Egentlige Aula-aftaler (fx forældremøde) vises stadig.
- Tekst fra Aula (beskeder, ugeplan) ryddes op: markdown-rester fjernes, hårde linjeskift samles til afsnit, og lister vises som lister.

## Kør som server (server.py)

`server.py` er én tjeneste, der gør tre ting: den **henter selv** fra Aula og Google (hvert kvarter om dagen, hvert andet time om natten), **serverer appen** bag familiens adgangskode, og har en side til **MitID-login**, så du ikke behøver en terminal. Den erstatter `fetch_family.py --watch` og `python -m http.server`.

**Hvor kan den køre?** På en maskine, der er tændt hele tiden og kan køre Python 3.14 eller Docker: en Raspberry Pi, en NAS, en lille VPS eller din egen pc. Almindeligt webhotel (PHP/cPanel) kan ikke, fordi det kræver en proces, der kører hele tiden.

### Windows (PowerShell), trin for trin

Første gang, i projektmappen:

```powershell
cd C:\sti\til\family-planner
uv venv --python 3.14                      # laver et "virtuelt miljø" (.venv) med appens egen Python
.\.venv\Scripts\Activate.ps1               # slå det til – prompten starter nu med (family-planner)
uv pip install -r requirements.txt         # installér appens pakker i miljøet
Copy-Item .env.example .env                # udfyld .env (se nedenfor)
```

Siger PowerShell *"running scripts is disabled"*, så kør én gang `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` og prøv igen.

**Hemmeligheder** (adgangskode, kode til private samtaler, AI-nøgle) kan stå to steder – vælg ét:

- **I `.env`** i projektmappen, én pr. linje, uden mellemrum og anførselstegn: `FAMILIEPLAN_PASSWORD=en-lang-adgangskode`. Appen læser selv filen. `.env` er udelukket fra git.
- **Med `setx`**: `setx FAMILIEPLAN_PASSWORD "en-lang-adgangskode"`. Virker først i et **nyt** PowerShell-vindue (eller efter genstart af VS Code). Er en variabel sat begge steder, vinder `setx`.

Brug ikke `$env:NAVN = "…"` til hemmeligheder: det gælder kun i det vindue, og er væk, når det lukkes.

Hver gang du starter appen:

```powershell
cd C:\sti\til\family-planner
.\.venv\Scripts\Activate.ps1
python server.py --selftest --no-notify    # tjek at alt er i orden
python server.py --host 0.0.0.0 --port 8080
```

Åbn http://localhost:8080 (andre enheder: http://maskinens-ip:8080). Log ind med adgangskoden. Står der "Aula-login er udløbet", så tryk på advarslen, vælg "Log ind med MitID", og scan QR-koden med MitID-appen.

Får du `ModuleNotFoundError` (fx *No module named 'fastapi'*), er det virtuelle miljø ikke slået til – kør `.\.venv\Scripts\Activate.ps1`. Mangler en af appens egne filer (fx *No module named 'activities'*), så kør `git status`; står der `deleted:`, henter `git restore .` dem tilbage uden at røre `config.toml`, `.env` eller jeres data. Kør aldrig `git add` før det er rettet.

Tip: Læg projektmappen uden for Skrivebordet (fx `C:\dev\family-planner`), hvis OneDrive synkroniserer Skrivebordet – synkroniseringsprogrammer kan fjerne eller låse filer.

### Docker (Linux, NAS, Raspberry Pi)

```bash
cp .env.example .env                       # sæt FAMILIEPLAN_PASSWORD
mkdir data && cp config.example.toml data/config.toml    # udfyld som før
docker compose up -d --build
```

Alt, der skal gemmes, ligger i `data/` (config, Aula-login og hentede data), så en opdatering er `docker compose up -d --build`. Uden Docker: se `familieplan.service` (systemd).

### Adgang udefra – vælg én

| Løsning | Åbne porte | Bemærkning |
|---|---|---|
| Kun hjemmenetværk (`BIND=0.0.0.0` i `.env`) | nej | Enklest. Adgangskoden sendes uden kryptering på dit eget netværk. |
| **Tailscale** på serveren og telefonerne | nej | Anbefalet til adgang på farten: kun jeres egne enheder kan nå den. |
| Cloudflare Tunnel | nej | Giver et offentligt navn med HTTPS uden at åbne porte. Overvej at lægge Cloudflare Access foran. |
| Caddy + eget domæne (`docker-compose.https.yml`) | 80 og 443 | Automatisk HTTPS. Sæt `trust_proxy = true` i `[server]`. |

**Udsæt aldrig port 8080 direkte på internettet uden HTTPS.** Appen indeholder børnenes skoledata.

### MitID-login og udløb

Aulas login fornyes automatisk, så MitID kun skal bruges, når fornyelsen fejler. Sker det, fortsætter serveren med de seneste Aula-data, viser en rød advarsel i appen og sender (hvis `notify_ntfy` er sat) en push-besked uden data i. Åbn `/auth` og log ind igen. Google-kalenderen opdateres uanset.

### "Føj til kalender"

Appen opretter **aldrig selv** aftaler eller forslag til kalenderen. Du vælger det, der skal med.

På *enhver* besked, ethvert opslag og ugeplanspunkt, hvor der kan udledes en **entydig dato og mindst et starttidspunkt**, står en knap "Føj til kalender". Den åbner en dialog, hvor du kan rette titel, dato, tid, sted og beskrivelse, før noget oprettes. Øverst står, hvilken kalender aftalen oprettes i, og hvem den tildeles ud fra navnene i titlen. Datoer kan skrives i de fleste former (`12/10`, `12.10.2026`, `12. okt`, `1210`, `i morgen`, `mandag`) eller vælges i en kalender; ugedagen vises under feltet, så du kan se, at du har ramt den rigtige dag. Har en sætning flere forskellige datoer, eller mangler der et tidspunkt, vises ingen knap.

Titlen er forudfyldt ud fra teksten ("Lejrskole", "Tur til Zoo", "Karlas fødselsdag"). Datoer regnes ud fra, hvornår beskeden er skrevet, så "testen i morgen (torsdag)" og "på fredag" bliver til rigtige datoer. Findes der allerede en aftale samme dag med samme ord, vises knappen ikke.

Aftalen oprettes med børnenes navne i titlen ("Hugo + Carla: Tur til Zoo"), fordi appen tildeler kalenderaftaler til personer ud fra navne i titlen. Beskrivelsen peger tilbage på beskeden. Aftaler, du opretter via appen, vises med det samme. Lige efter oprettelsen kan du trykke "Fortryd" i bekræftelsen. Angiver du kun et starttidspunkt, får Google en sluttid en time senere, men appen viser kun starttidspunktet.

### Sæt direkte oprettelse op (én gang)

Appen opretter aftaler direkte i familiekalenderen – du forlader aldrig appen, og aftalen kan ikke havne i en forkert kalender. Det kræver, at serveren har lov til at skrive til kalenderen. Det sker med en *servicekonto* (en robotbruger), som familiekalenderen deles med. Indtil det er sat op, er knappen "Opret i kalender" slået fra, og appen skriver hvorfor.

**Tjekliste** (ca. 15 minutter):

- [ ] Gå til console.cloud.google.com og opret et projekt, fx "Familieplan".
- [ ] Slå **Google Calendar API** til (APIs & Services → Library).
- [ ] Opret en servicekonto (IAM & Admin → Service Accounts → Create). Den behøver ingen roller.
- [ ] Åbn servicekontoen → Keys → Add key → Create new key → **JSON**. Gem filen som `secrets/google_service_account.json` (i Docker: `data/secrets/`). Behandl den som en adgangskode.
- [ ] Kopiér servicekontoens e-mailadresse (slutter på `.iam.gserviceaccount.com`).
- [ ] I Google Kalender: familiekalenderens indstillinger → **Del med bestemte personer eller grupper** → tilføj e-mailadressen med tilladelsen **Foretag ændringer i begivenheder**.
- [ ] I `config.toml`: sæt `write = true` på familiekalenderen under `[[google]]` (nødvendigt, hvis du har flere Google-kalendere – appen gætter aldrig), og `enabled = true` under `[calendar_write]`. Kalender-id'et udledes af iCal-adressen; ellers sæt `calendar_id` på kalenderen.
- [ ] Påmindelser: hver forælder åbner familiekalenderens indstillinger i Google Kalender → **Standardunderretninger** og vælger fx "1 dag før". Google giver kun påmindelser til den, der opretter en aftale – her servicekontoen – så appen kan ikke sætte dem for jer. Fjern `reminder_minutes`, hvis den står i din config.
- [ ] Genstart serveren og kør `python server.py --selftest`. Den opretter og sletter en prøveaftale langt ude i fremtiden og tjekker, at kalenderen også kan læses via API'et. Alt skal være ✔.
- [ ] Prøv i appen: tryk "Føj til kalender" på en besked, og se at dialogen skriver "Oprettes i **Familiekalender**", at aftalen dukker op i Google Kalender, og at "Fortryd" i bekræftelsen fjerner den igen.

Menuernes navne hos Google kan ændre sig lidt. Får du en fejl, forklarer appen den (fx "er kalenderen delt med servicekontoen?"). Hver aftale får et fast id, så den aldrig kan oprettes to gange, og "Fjern fra kalender" sletter den igen.

**Læsning via API.** Når servicekontoen virker, læses familiekalenderen også via Googles API i stedet for iCal-adressen (`read_via_api = true`, standard). Ændringer ses så ved næste hentning i stedet for efter Googles iCal-forsinkelse, og appen kan kende de aftaler, den selv har oprettet. Fejler API'et, bruges iCal-adressen som reserve, hvis den står i config. Andre Google-kalendere læses stadig via iCal.

Indstillinger: `[suggestions]` (`enabled` slår genkendelsen af datoer og aflysninger fra, `horizon_days`, `max_age_days`) og `[calendar_write]` (`calendar_id`, `read_via_api`). Test genkendelsen på dine egne data uden Aula: `python activities_test.py aula_feed.json`.

## Markér som læst

Ulæste beskeder har en blå prik i margenen og fed skrift, og i læseruden et "Ulæst"-mærke. Som i Outlook bliver en besked læst, når den har stået åben i **3 sekunder** – ikke når man bare bladrer forbi den. Åbner man den og går tilbage inden da, er den stadig ulæst. En besked, der automatisk er valgt på en stor skærm, markeres ikke; det kræver et klik. Private samtaler, der er skjult, markeres først, når man har trykket "Vis besked".

- **Knap:** "Markér som læst" i læseruden (på computer også tasten `m`).
- **Touch (iPad, telefon):** stryg en ulæst besked mod venstre. Et kort stryg viser en blå "Læst"-knap; et langt stryg markerer med det samme.
- **I Aula:** med serveren kører markeringen også i Aula (`mark_read_in_aula = true` under `[server]`), og alle enheder ser den som læst. Kaldet køres i baggrunden, samles og prøves igen, hvis Aula svarer med en fejl; hvis Aula-loginet er udløbet, venter markeringerne. Slå det fra med `mark_read_in_aula = false`, så gælder markeringen kun på den enhed, hvor du læste beskeden.
- Bemærk: Aulas "læst" kan være synlig for andre (fx afsender). Det er ikke afprøvet. Der findes ikke noget kald til at markere som *ulæst* igen.

## iPad, telefon og computer

Layoutet tilpasser sig skærmen:

| Skærm | Opbygning |
|---|---|
| Computer og iPad liggende | Ugen som tavle med en kolonne pr. dag. Beskeder med liste og læserude ved siden af hinanden. Feedet i én kolonne, bredere på store skærme (560 → 720 → 860 px). |
| iPad stående | Ugen som dagkort i to spor, med i dag først. Beskeder stadig med to ruder. |
| Telefon | Menuen ligger i bunden som i en app. Personfiltrene står på én række. Beskeder viser én rude ad gangen, og læsevisningen fylder hele skærmen. |

Alt, der kan trykkes på, er mindst 44 px på berøringsskærme. Layoutet respekterer iPhones og iPads sikre områder (hak og hjemmelinje), og appen henter frisk data, når tabletten vågner af dvale.

**Læg den på hjemmeskærmen (iPad/iPhone):** åbn adressen i Safari → Del → "Føj til hjemmeskærm". Så åbner den uden Safaris adresselinje, med eget ikon. Til en tablet, der står fremme i køkkenet: Indstillinger → Skærm & lysstyrke → Autolås, og evt. Tilgængelighed → Guidet adgang, så den ikke slukker eller forlades ved et uheld.

Afprøvet i Chromium med emulerede iPad- og iPhone-størrelser og berøringsgester, men ikke i selve Safari på en iPad.

## Sikkerhed

- Adgangskode (mindst 8 tegn), cookie der kun kan læses af serveren, 90 dages login pr. enhed, og spærring efter 5 forkerte forsøg. Skift adgangskoden, og alle logges ud.
- `data/secrets/aula_tokens.json` giver adgang til Aula som forælder. Behandl den som en adgangskode, og tag den ikke med i åbne sikkerhedskopier.
- Billeder, beskeder og `family.json` serveres kun til indloggede. **Private samtaler ligger ikke i `family.json`**, men bag en ekstra kode (se "Private samtaler").
- Serveren kører som almindelig bruger i containeren, og `config.toml` og `data/` kopieres aldrig ind i Docker-billedet (`.dockerignore`).

### Indstillinger (`[server]` i config.toml)

`interval_minutes`, `night_interval_minutes`, `aula`, `session_days`, `trust_proxy`, `public_url`, `notify_ntfy`, `mark_read_in_aula`, `evening_push`, `evening_push_only_if_content`, `push_details`, `stale_alert_hours`, `private_unlock_minutes`. Dertil `[display] evening_hour` og `[private] protect`. Kun én serverproces må køre ad gangen.

### Filer og netværk

- `config.toml` indeholder den hemmelige iCal-adresse, og `secrets/` indeholder Aula-login. Ingen af dem må ligge i `web/`. Serveren nægter desuden at udlevere filer som `private_messages.json`, `*_state.json`, `learned_rules.json` og `config.toml`, uanset hvor datamappen ligger.
- Kør det kun på jeres eget netværk eller bag HTTPS – `family.json` og `media/` indeholder børnenes skoledata og billeder.

## Aftenvisning og aftenpush

Efter kl. 17 (`[display] evening_hour`) handler fanen **I dag** om i morgen: overblik, skema, aftaler, vigtig info og opgaver. Skiftet sker af sig selv kl. 17 og tilbage ved midnat; der er ingen knap til at skifte. Resten af dagen i dag kan ses under **Ugen**. Serveren laver overblikket for i morgen præcis kl. 17, ikke først ved næste kvarter, og frister måles stadig mod den rigtige dato ("senest i morgen", ikke "i dag").

Med `notify_ntfy` sat sender serveren **én besked om dagen** efter kl. 17 om i morgen, kun hvis der er noget at huske, gøre eller noget særligt (`evening_push_only_if_content = false` sender altid). Pushet sendes højst én gang pr. dag, også efter en genstart.

> **Privatliv:** på den offentlige `ntfy.sh` kan alle, der kender emnets navn, læse beskederne. Derfor indeholder beskeden som standard kun tal ("2 at huske · 1 skal gøres · 1 særligt") og ingen navne. `push_details = "full"` tager punkterne i klar tekst med – brug det kun på din egen ntfy-server.

## Kioskvisning

En stor, rolig vægvisning til en tablet i køkkenet. Start den med knappen **Kioskvisning** i statuslinjen, eller åbn adressen med `?kiosk=1` (fx som genvej på hjemmeskærmen, så starter den direkte).

- Ur, dato og dagen (I dag / I morgen, efter samme regel som ovenfor), et kort pr. barn (skoletid, hvad der er nu og næste, vikarer, ting at huske) samt Husk og frister og dagens aftaler.
- Teksten tilpasser sig skærmen, så alt kan ses uden at rulle. Data hentes på ny hvert andet minut, og skærmen holdes tændt (Wake Lock, hvor enheden understøtter det; ellers sæt Autolås til "Aldrig").
- **Viser aldrig beskeder** og intet fra private samtaler.
- Tryk et vilkårligt sted for at få **Afslut** frem i seks sekunder (på tastatur: `Esc`). Øverst står en rød advarsel, hvis data er over en time gamle, Aula-login er udløbet, eller Google Kalender ikke kunne hentes.

## Private samtaler

Samtaler mellem jer og personalet (fx om et barns trivsel) var tidligere kun skjult på skærmen – teksten lå stadig i den fil, alle enheder henter. Nu gælder:

- I `family.json` står kun "Privat samtale", tidspunktet og hvilket barn det handler om. **Indholdet** ligger i `secrets/private_messages.json` (rettigheder 600, sammen med Aula-nøglerne og aldrig i den mappe, der serveres).
- Serveren udleverer det først, når du har indtastet koden i miljøvariablen `FAMILIEPLAN_PRIVATE_CODE` (mindst 4 tegn, helst en anden end adgangskoden). Fem forkerte forsøg spærrer i fem minutter.
- Oplåsningen gælder `private_unlock_minutes` (standard 10). Appen låser desuden, når du forlader Beskeder, og når skærmen slukkes eller appen skiftes. Billeder fra private samtaler kræver også oplåsning.
- Private tråde indgår aldrig i overblik, forslag, opgaver eller søgning, og de hentes ikke forfra fra Aula ved hver kørsel.
- Er `FAMILIEPLAN_PRIVATE_CODE` ikke sat, kan private samtaler ikke åbnes i appen.
- Bruger du appen uden serveren (fx `python -m http.server`), kan intet beskyttes: sæt `[private] protect = false`, og de ligger som før i `family.json`.

## Aflysninger og flytninger

En besked om, at noget er **aflyst, udsat eller flyttet**, bliver ikke til en ny aftale, men til en ændring af den, der allerede står i kalenderen. Ændringen står **på beskeden selv** (og på opslaget eller ugeplanspunktet) under "Ændring i kalenderen":

| Beskeden siger | Appen foreslår |
|---|---|
| "Turen er aflyst" / "ingen tur i år" | **Fjern fra kalender** (efter en bekræftelse), hvis appen har oprettet aftalen |
| "Forældremødet flyttes til 4. nov." / "flyttet fra 21/10 til 4/11" | **Flyt aftalen** (åbner dialogen med den nye tid) |
| "Udsat på ubestemt tid" | Fjern, eller behold til der kommer en ny dato |

"Behold" lader aftalen stå og kan fortrydes.

- Aftaler, som **ikke** er oprettet via appen, kan appen ikke ændre sikkert. De får en besked om at rette dem i Google Kalender.
- Findes der ingen aftale at aflyse, sker der ingenting. Ved en flytning uden kendt gammel aftale kan du bruge "Føj til kalender" på beskeden med den **nye** dato.
- Spørgsmål ("Skal vi på tur?"), betingelser og tvetydige tilfælde (to mulige aftaler) giver ikke noget gæt. En senere aflysning fjerner også "Føj til kalender"-muligheder om det samme.
- Klokkeslæt som "kl. 7 om aftenen" læses som 19.00.

## Drift, tilsyn og selvtest

- **Planlæggeren dør ikke lydløst.** En uventet fejl logges, vises i `/api/status` (`internal_errors`), og næste kørsel går som normalt. Selve baggrundsopgaverne genstartes automatisk, hvis de går ned.
- **Google nede ≠ tom kalender.** Kan en Google-kalender ikke hentes, beholdes dens seneste aftaler, og appen viser en advarsel i overskriften. Aula-data genbruges som før.
- **Tilsyn.** Er data (eller Aula-data) ældre end `stale_alert_hours` (standard 4), får du en ntfy-besked – én gang pr. døgn pr. problem, aldrig om natten – og en besked, når det virker igen. Et udløbet Aula-login har sin egen besked.
- **Selvtest:** `python server.py --selftest` (tilføj `--no-notify` for ikke at sende en prøvebesked). Den tjekker Python-version, config, adgangskoder, rettigheder på nøglefiler, iCal-adressen, Aula-login (børn, sideinddeling, markér-som-læst), Google-skrivning (opretter og sletter en prøveaftale langt ude i fremtiden), ntfy, og at ingen privat tekst ligger i `family.json`. Svaret er en liste med ✔ (virker), ⚠ (virker, men ret det) og ✖ (virker ikke) og en forklaring på, hvad du skal gøre. Slutkoden er 1, hvis noget er ✖. **Kør den første gang, du sætter det op.**

## Opdatér afhængigheder

`requirements.txt` er **fastlåst** (også de indirekte pakker) til Python 3.14, så en ny version af en pakke ikke kan ændre noget uden varsel – særligt vigtigt for `aula`, som er uofficiel. Det er `requirements.in`, du redigerer. Efter en ændring:

```
uv pip compile requirements.in --python-version 3.14 --universal --annotation-style line -o requirements.txt
uv pip compile requirements-dev.in --python-version 3.14 --universal --annotation-style line -o requirements-dev.txt
```

Kør derefter testene (næste afsnit), før du bygger billedet igen.

## Test

```
pip install -r requirements.txt -r requirements-dev.txt   # eller: uv pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium          # kun til browsertestene
pytest                                         # alt
pytest --ignore=tests/browser                  # kun serverdelen (hurtig, uden browser)
```

Testene bruger **opfundne data og simulerede tjenester** (en Google Kalender, der kontrollerer servicekontoens signatur, en ntfy-modtager og en iCal-server), så de rører hverken Aula, din rigtige kalender eller dine data.

| Fil | Dækker |
|---|---|
| `test_activities.py` | genkendelse af aktiviteter; aflysning, udsættelse, flytning |
| `test_pipeline.py` | hele hentningen: Google-udfald, private tråde, ændringer mod rigtige aftaler, overblik efter kl. 17 |
| `test_ops.py` | planlægger, aftenpush, tilsyn, push-beskeder |
| `test_gcal.py` | oprettelse, flytning, sletning i Google Kalender; valg af skrivekalender |
| `test_google_api_read.py` | familiekalenderen læst via Google API, sider, reserve til iCal |
| `test_times.py` | sluttider: rigtige, tænkte (`endInferred`) og 45-minutters lektioner |
| `test_server.py` | adgang, CSRF, spærring, private tråde og billeder, aflys/flyt |
| `test_selftest.py` | selvtesten mod sunde og ødelagte opsætninger |
| `browser/` | datovælger, layout, beskeder (læst, stryg, private), "Føj til kalender" (oprettelse, fortryd, modtager), aflysninger på beskeden, søgning i feedet, tider i alle visninger, aften og kiosk, tilgængelighed |

`homework_test.py` og `messages_test.py` i roden er ældre hjælpescripts, der kører genkendelsen mod **dine egne filer** (`python homework_test.py weekplan.json`, `python messages_test.py aula_feed.json`) og indgår ikke i `pytest`.

Tilgængelighedstesten er valgfri: `npm install axe-core`, og sæt `AXE_JS=…/node_modules/axe-core/axe.min.js`.

## Kendte begrænsninger

- Aula-adgangen er uofficiel (via `nickknissen/aula`) og kan gå i stykker, når Aula ændrer noget. Scriptet genbruger så de seneste Aula-data, og appen viser en advarsel, når data er over en time gamle.
- Lektier hentes kun, hvis skolen bruger Min Uddannelse. Meebook/EasyIQ kan tilføjes senere.
- Hvis MitID-tokens udløber, skal du køre scriptet interaktivt igen og scanne QR-koden.
- **Aula og Google er kun afprøvet mod simuleringer.** Loginet, sideinddelingen, markér-som-læst, skrivningen til og læsningen fra Google Kalender via API og påmindelsernes adfærd er aldrig kørt mod de rigtige tjenester; det er det, `--selftest` er til. Docker-billedet er heller ikke bygget i udviklingsmiljøet.
- Private samtaler kan ikke beskyttes uden serveren (se ovenfor). Aulas "læst" kan være synlig for andre.
- Aflysninger og flytninger kan kun ændre aftaler, appen selv har oprettet.
