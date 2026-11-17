# RMS database extractor

Reads the RMS SQLAlchemy database and writes `risk_master`, `risk_cycle`, and `risk_action` rows to Cognite Raw. The extractor follows the existing extractor service/configuration conventions in this repository.

## Configure current SQLite source

Run the extractor on the VM with access to the SQLite file. Set `RMS_DB_URL` to the actual database file path; SQLAlchemy SQLite URLs use four slashes for an absolute Unix path:

```bash
export RMS_DB_URL='sqlite:////absolute/path/to/risk.db'
```

For a relative file in the working directory, use `sqlite:///./risk.db`. Ensure the extractor service account can read the file and its containing directory.

## Switch to PostgreSQL

The extractor uses SQLAlchemy, so switching database engines only requires changing the URL and installing the PostgreSQL driver. The PostgreSQL development endpoint uses port 5432 unless the DB administrator specifies otherwise. Obtain the database name and credentials from the authorized secret manager/DB administrator; do not commit credentials to this repository.

```bash
export RMS_DB_URL='postgresql+psycopg://<user>:<password>@<host>:5432/<database>?sslmode=require'
```

Install the driver before the migration:

```bash
pip install 'psycopg[binary]>=3.2'
```

The repository includes this optional dependency in `pyproject.toml`; install with `pip install '.[postgres]'` from this directory when PostgreSQL is needed. The SQLite configuration remains the default until `RMS_DB_URL` is changed.

## Cognite and state configuration

Provide `CDF_URL`, `CDF_PROJECT`, `IDP_TOKEN_URL`, `IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`, `CDF_RAW_DB`, and `CDF_RAW_STATE_TABLE` as environment variables. `ep_rms_ingestion` is the current default pipeline external ID in `config.yaml`; adjust it to the provisioned pipeline if needed.

Raw destinations are `<CDF_RAW_DB>.risk_master`, `<CDF_RAW_DB>.risk_cycle`, and `<CDF_RAW_DB>.risk_action`. The last successful run timestamp is stored in the configured state table. Each table is read in batches, and its primary key is used as the Raw row key.

## Network/security notes

The provided PostgreSQL server address is a private Azure endpoint. The extractor VM must have an approved private network route and DB access-control rule to that server on TCP 5432. Confirm connectivity from the VM with the DB/network administrator. The VM information in the supplied image identifies the development RMS application VM as `ho-cog-d-rms01` (`10.135.29.80`); use the actual mounted SQLite file on that VM. Do not expose the database publicly or place database passwords in config files, source control, or command-line history. The password included in the chat should be rotated and stored in an approved secret store.

## Run locally

```bash
cd extractors/rms
pip install '.[postgres]'  # or install the default dependencies for SQLite only
python -m rms_extractor
```
