# Hanwha Ocean - Primavera P6 Extractor

This extractor retrieves project and activity records from Oracle Primavera P6
EPPM REST endpoints and writes them to CDF Raw.

## Configuration

Set the CDF and P6 credentials in the environment, then configure `config.yaml`:

- `P6_BASE_URL`: base URL of the P6 REST API.
- `P6_USERNAME` / `P6_PASSWORD`: P6 API credentials. Basic authentication is
  used as the initial assumption, following the SCM extractor pattern.
- `project_data.endpoint` / `activity_data.endpoint`: endpoint paths relative
  to `base_url`.
- `raw_table`: CDF Raw destination for each data type.
- `raw_key`: field used as the Raw row key.

P6 installations may expose different REST paths and response/pagination
formats. Configure `response_items_key`, `has_more_field`, and the pagination
parameter names to match the target API. Set a pagination parameter to an empty
string when that endpoint does not support it. The default response format is
a JSON list or an object containing an `items` list.

The extractor currently performs a full query for each configured endpoint on
every run; it does not apply an incremental P6 filter. Confirm the target P6
API version, endpoint paths, response schema, and pagination behavior before
production use.

## Run

From this directory, install the project and run:

```shell
python -m p6_extractor
```

The Windows service entry point is `p6_extractor.winservice`.

See [P6_EXECUTION_GUIDE.md](./P6_EXECUTION_GUIDE.md) for the end-to-end
execution flow, likely changes, and information to confirm before testing.
