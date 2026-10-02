# Hanwha Ocean - Aveva Custom Technical Extractor

## Purpose
This is a customer extractor build to export data tabular data from Aveva to CDF Raw tables based on the cognite-extractor-manager package.

*source*
- tabular data from Aveva specified by datasource, category and table names. Data is retrieved from the Aveva via API calls.
- each source can contain one or more chunks of data.  Individual chunks are added to a queue for processing and upload to CDF raw/staging.

*target*
- Raw tables within CDF. All data is store within the "aveva_technical" database.
- Raw table names are a colon separated concatenation of the Aveva data source, category and table name the information was retrieved from.
- The keys of the raw entries are a colon separated concatentation of the data chunk number and index of the data within the chunk.
