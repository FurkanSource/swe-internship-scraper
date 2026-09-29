# Getting started from a GitHub checkout

Use this guide if you want to run or modify the source code. If you only want
internship results, the two-command [Quick start](../README.md#quick-start) does
not require Git or a repository download.

Python 3.10–3.14 is required. Install Git only if you choose the clone command;
GitHub's **Code → Download ZIP** works too. Extract the ZIP and open a terminal
in the extracted `swe-internship-scraper` folder before running the commands below.

## Windows PowerShell

If you downloaded a ZIP, skip the first two lines and open PowerShell in its
extracted folder.

```powershell
git clone https://github.com/FurkanSource/swe-internship-scraper.git
cd swe-internship-scraper
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m swe_scraper scan --quick --output internships.csv
```

Open `internships.csv` in a spreadsheet. The virtual environment stays inside
`.venv`; no PowerShell activation or change to the execution policy is needed.
Keep using that environment's executable. A global `py -m swe_scraper` can load
another editable checkout even when your terminal is in this repository.
To check which source is running:

```powershell
.\.venv\Scripts\python.exe -c "import swe_scraper; print(swe_scraper.__file__)"
```

The path should point into this checkout's `src/swe_scraper` directory.

## macOS and Linux

If you downloaded a ZIP, skip the first two lines and open a terminal in its
extracted folder.

```sh
git clone https://github.com/FurkanSource/swe-internship-scraper.git
cd swe-internship-scraper
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m swe_scraper scan --quick --output internships.csv
```

`--quick` scans a small monitored sample. Remove it to scan the default priority
boards. Use `--target-set all` for the entire catalog; that takes longer and
contacts more public job boards. See the [command guide](command-line.md) for
filters, custom targets, and other output formats.

If you want to contribute code, follow the [project guide](../PROJECT_GUIDE.md#contributing)
after this first run.
