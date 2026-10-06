# Hanwha Ocean - SCM Custom Extractor

## Purpose
This is a customer extractor build to export data from SCM based on the cognite-extractor-manager package.

*source*
- the extractor exports supplier, purchase order, RFQ, etc. data from SCM repository via the API.

*target*
- data is stored in Raw tables in CDF.

## Understanding the execution flow

See [SCM_EXECUTION_GUIDE.md](./SCM_EXECUTION_GUIDE.md) for the end-to-end startup,
query, upload, state-store, and error-handling flow.
