CREATE TABLE bom_line (
  cpn TEXT PRIMARY KEY,
  mpn TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty > 0)
);

SELECT cpn, qty FROM bom_line WHERE qty > 10 ORDER BY cpn;
