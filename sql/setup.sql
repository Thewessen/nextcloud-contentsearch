CREATE ROLE contentsearch_indexer LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE nextcloud TO contentsearch_indexer;
GRANT SELECT ON oc_filecache, oc_storages, oc_mimetypes TO contentsearch_indexer;

CREATE TABLE oc_contentsearch (
    fileid     bigint PRIMARY KEY,
    storage    bigint NOT NULL,
    etag       text NOT NULL,
    status     text NOT NULL,          -- ok | needs_ocr | ocr | empty | error
    content    text,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('dutch', coalesce(content, ''))) STORED,
    indexed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX oc_contentsearch_tsv ON oc_contentsearch USING gin (tsv);
ALTER TABLE oc_contentsearch OWNER TO contentsearch_indexer;
GRANT SELECT ON oc_contentsearch TO nextcloud;
