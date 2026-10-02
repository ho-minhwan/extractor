# Hanwha Ocean - ProArc Custom Extractor
# Testing CI in ProArc

## Purpose
This is a customer extractor build to export data from ProArc based on the cognite-extractor-manager package.

*source*
- the extractor export document, document revision and file data from the ProArc repository via the API.

*target*
- metadata for the documents, revisions and files is stored in the Raw tables in CDF.
- files are uploaded to CDF and stored as CogniteFiles.
