DROP TABLE IF EXISTS translation;

CREATE TABLE translation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  value TEXT NOT NULL DEFAULT ''
);
