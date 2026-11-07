"""Build a minimal Zotero-schema SQLite DB for aikeytake_zog tests (plan Task 10).

`build(path, storage_dir=None)` creates a small but schema-faithful
zotero.sqlite with the content required by the plan: two normal items (a
journal article with a PDF carrying two annotations, a webpage with an HTML
snapshot), an imported_url PDF, a linked file whose path does not exist, a
linked URL, a trashed item, a trashed collection, a standalone note, a child
note, an orphan attachment, a nested collection pair, tags, a single-field
creator, a "YYYY-MM-DD original" date and a group-library item.

If `storage_dir` is given, placeholder storage files are written for the
`storage:` attachments (a fake PDF body and a snapshot dir with an asset).

Returns a dict of the created keys, e.g.
{"items": {"paper": "ITEMAAAA", ...}, "attachments": {...}, ...}.
"""
import sqlite3
from pathlib import Path

# --- fixed ids/keys ---------------------------------------------------------
T_ANNOTATION, T_ATTACHMENT, T_JOURNAL, T_NOTE, T_WEBPAGE = 1, 3, 22, 28, 40
F_TITLE, F_ABSTRACT, F_DATE, F_URL, F_DOI, F_PUBTITLE = 1, 2, 6, 10, 11, 12

I_PAPER, I_WEB, I_URLPAPER, I_LINKED, I_LINKURL, I_TRASHED, I_GROUP = 101, 102, 103, 104, 105, 106, 206
I_NOTE, I_ORPHAN_ATT, I_CHILD_NOTE = 107, 108, 109

A_PAPER, A_SNAP, A_URLPDF, A_MISSING, A_LINK = 201, 202, 203, 204, 205
A_DANGLING = 208  # parentItemID points at a non-existent item
# the orphan attachment item and its itemAttachments row share itemID I_ORPHAN_ATT
ANN_1, ANN_2 = 301, 302

C_PARENT, C_CHILD, C_TRASHED = 401, 402, 403
TAG_ML, TAG_WEB = 501, 502
CR_CORP, CR_DOE = 601, 602

KEYS = {
    "items": {
        "paper": ("ITEMAAAA", I_PAPER),
        "webpage": ("ITEMEEEE", I_WEB),
        "urlpaper": ("ITEMGGGG", I_URLPAPER),
        "linked": ("ITEMIIII", I_LINKED),
        "linkurl": ("ITEMKKKK", I_LINKURL),
        "trashed": ("ITEMMMMM", I_TRASHED),
        "group": ("GROUPNNN", I_GROUP),
        "note": ("NOTEoooo", I_NOTE),
        # an orphan attachment item: same key as its itemAttachments row
        "orphan": ("ATTpPPPp", I_ORPHAN_ATT),
    },
    "attachments": {
        "paper": ("ATTBBBBB", A_PAPER),
        "snap": ("ATTFFFFF", A_SNAP),
        "urlpdf": ("ATTHHHHH", A_URLPDF),
        "missing": ("ATTJJJJJ", A_MISSING),
        "link": ("ATTLLLLL", A_LINK),
        # the orphan's attachment row reuses the item's key/itemID
        "orphan": ("ATTpPPPp", I_ORPHAN_ATT),
    },
    "annotations": {"a1": ("ANNCCCCC", ANN_1), "a2": ("ANNDDEED", ANN_2),
                    "a3": ("ANNEEEEE", 303)},
    "attachments_extra": {"dangling": ("ATTrRRRr", A_DANGLING)},
    "notes": {"standalone": ("NOTEoooo", I_NOTE), "child": ("NOTEqqQQ", I_CHILD_NOTE)},
    "collections": {
        "parent": ("COLPARENT", C_PARENT),
        "child": ("COLCHILD", C_CHILD),
        "trashed": ("COLTRASH", C_TRASHED),
    },
}

SCHEMA = """
CREATE TABLE items(itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, dateAdded TEXT,
  dateModified TEXT, clientDateModified TEXT, libraryID INTEGER, key TEXT, version INTEGER,
  clientVersion INTEGER, synced INTEGER);
CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE fields(fieldID INTEGER PRIMARY KEY, fieldName TEXT, fieldFormatID INTEGER);
CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER, PRIMARY KEY(itemID, fieldID));
CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value TEXT, valueNormalized TEXT);
CREATE TABLE creators(creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT,
  fieldMode INTEGER, firstNameNormalized TEXT, lastNameNormalized TEXT);
CREATE TABLE itemCreators(itemID INTEGER, creatorID INTEGER, creatorTypeID INTEGER, orderIndex INTEGER);
CREATE TABLE creatorTypes(creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE collections(collectionID INTEGER PRIMARY KEY, collectionName TEXT,
  parentCollectionID INTEGER, clientDateModified TEXT, libraryID INTEGER, key TEXT,
  version INTEGER, clientVersion INTEGER, synced INTEGER);
CREATE TABLE collectionItems(collectionID INTEGER, itemID INTEGER, orderIndex INTEGER);
CREATE TABLE tags(tagID INTEGER PRIMARY KEY, name TEXT, nameNormalized TEXT);
CREATE TABLE itemTags(itemID INTEGER, tagID INTEGER, type INTEGER);
CREATE TABLE itemNotes(itemID INTEGER PRIMARY KEY, parentItemID INTEGER, note TEXT, title TEXT);
CREATE TABLE itemAttachments(itemID INTEGER PRIMARY KEY, parentItemID INTEGER, linkMode INTEGER,
  contentType TEXT, charsetID INTEGER, path TEXT, syncState INTEGER, storageModTime TEXT,
  storageHash TEXT, lastProcessedModificationTime TEXT, lastRead TEXT);
CREATE TABLE itemAnnotations(itemID INTEGER PRIMARY KEY, parentItemID INTEGER, type INTEGER,
  authorName TEXT, text TEXT, textNormalized TEXT, comment TEXT, commentNormalized TEXT,
  color TEXT, pageLabel TEXT, sortIndex INTEGER, position TEXT, isExternal INTEGER);
CREATE TABLE deletedItems(itemID INTEGER, dateDeleted TEXT);
CREATE TABLE deletedCollections(collectionID INTEGER, dateDeleted TEXT);
CREATE TABLE libraries(libraryID INTEGER PRIMARY KEY, type TEXT, editable INTEGER,
  filesEditable INTEGER, version INTEGER, clientVersion INTEGER, storageVersion INTEGER,
  lastSync TEXT, archived INTEGER, isAdmin INTEGER);
"""


def build(path: Path | str, storage_dir: Path | str | None = None) -> dict:
    """Create the fixture Zotero DB at `path`; returns the created keys."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        c = conn.cursor()

        c.executemany(
            "INSERT INTO itemTypes VALUES(?,?)",
            [(T_ANNOTATION, "annotation"), (T_ATTACHMENT, "attachment"),
             (T_JOURNAL, "journalArticle"), (T_NOTE, "note"), (T_WEBPAGE, "webpage")],
        )
        c.executemany(
            "INSERT INTO fields VALUES(?,?,?)",
            [(F_TITLE, "title", 1), (F_ABSTRACT, "abstractNote", 1), (F_DATE, "date", 3),
             (F_URL, "url", 7), (F_DOI, "doi", 7), (F_PUBTITLE, "publicationTitle", 1)],
        )
        c.executemany(
            "INSERT INTO libraries VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(1, "user", 1, 1, 0, 0, 0, None, 0, 1), (2, "group", 0, 0, 0, 0, 0, None, 0, 0)],
        )

        def item(iid, key, lib, itype, added="2024-01-01 00:00:00"):
            c.execute(
                "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
                (iid, itype, added, added, added, lib, key, 1, 1, 1),
            )

        item(I_PAPER, "ITEMAAAA", 1, T_JOURNAL)
        item(I_WEB, "ITEMEEEE", 1, T_WEBPAGE)
        item(I_URLPAPER, "ITEMGGGG", 1, T_JOURNAL)
        item(I_LINKED, "ITEMIIII", 1, T_JOURNAL)
        item(I_LINKURL, "ITEMKKKK", 1, T_JOURNAL)
        item(I_TRASHED, "ITEMMMMM", 1, T_JOURNAL)
        item(I_GROUP, "GROUPNNN", 2, T_JOURNAL)
        item(I_NOTE, "NOTEoooo", 1, T_NOTE)
        item(I_ORPHAN_ATT, "ATTpPPPp", 1, T_ATTACHMENT)
        item(I_CHILD_NOTE, "NOTEqqQQ", 1, T_NOTE)
        item(A_PAPER, "ATTBBBBB", 1, T_ATTACHMENT)
        item(A_SNAP, "ATTFFFFF", 1, T_ATTACHMENT)
        item(A_URLPDF, "ATTHHHHH", 1, T_ATTACHMENT)
        item(A_MISSING, "ATTJJJJJ", 1, T_ATTACHMENT)
        item(A_LINK, "ATTLLLLL", 1, T_ATTACHMENT)
        item(A_DANGLING, "ATTrRRRr", 1, T_ATTACHMENT)
        item(ANN_1, "ANNCCCCC", 1, T_ANNOTATION)
        item(303, "ANNEEEEE", 1, T_ANNOTATION)
        item(ANN_2, "ANNDDEED", 1, T_ANNOTATION)

        _data = []
        next_vid = [1]

        def field(iid, fid, value):
            vid = next_vid[0]
            next_vid[0] += 1
            c.execute("INSERT INTO itemDataValues VALUES(?,?,?)", (vid, value, value))
            _data.append((iid, fid, vid))

        field(I_PAPER, F_TITLE, "PDF paper")
        field(I_PAPER, F_DATE, "2020-01-00 2020")
        field(I_PAPER, F_DOI, "10.1000/fixture")
        field(I_PAPER, F_ABSTRACT, "Abstract about zotofixture.")
        field(I_PAPER, F_PUBTITLE, "Fixture Journal")
        field(I_WEB, F_TITLE, "Web page")
        field(I_WEB, F_URL, "http://example.org/page")
        field(I_WEB, F_DATE, "2021-05-06 2021-05-06")
        field(I_URLPAPER, F_TITLE, "URL paper")
        field(I_URLPAPER, F_DATE, "2019-07-08 2019")
        field(I_LINKED, F_TITLE, "Linked files")
        field(I_LINKURL, F_TITLE, "Linked url")
        field(I_TRASHED, F_TITLE, "Trashed item")
        field(I_GROUP, F_TITLE, "Group item")
        c.executemany("INSERT INTO itemData VALUES(?,?,?)", _data)

        # creators: one single-field (fieldMode=1), one first/last
        c.execute("INSERT INTO creatorTypes VALUES(?,?)", (10, "author"))
        c.execute(
            "INSERT INTO creators VALUES(?,?,?,?,?,?)", (CR_CORP, "", "ACME Corp", 1, "", "")
        )
        c.execute(
            "INSERT INTO creators VALUES(?,?,?,?,?,?)", (CR_DOE, "Jane", "Doe", 0, "jane", "doe")
        )
        c.executemany(
            "INSERT INTO itemCreators VALUES(?,?,?,?)",
            [(I_PAPER, CR_CORP, 10, 0), (I_PAPER, CR_DOE, 10, 1)],
        )

        # collections: nested pair + trashed one
        c.execute(
            "INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)",
            (C_PARENT, "Parent col", None, None, 1, "COLPARENT", 1, 1, 1),
        )
        c.execute(
            "INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)",
            (C_CHILD, "Child col", C_PARENT, None, 1, "COLCHILD", 1, 1, 1),
        )
        c.execute(
            "INSERT INTO collections VALUES(?,?,?,?,?,?,?,?,?)",
            (C_TRASHED, "Trashed col", None, None, 1, "COLTRASH", 1, 1, 1),
        )
        c.executemany(
            "INSERT INTO collectionItems VALUES(?,?,?)",
            [(C_PARENT, I_PAPER, 0), (C_CHILD, I_WEB, 0)],
        )
        c.execute("INSERT INTO deletedCollections VALUES(?,?)", (C_TRASHED, "2024-02-02"))

        # tags
        c.executemany("INSERT INTO tags VALUES(?,?,?)", [(TAG_ML, "ml", "ml"), (TAG_WEB, "web", "web")])
        c.executemany(
            "INSERT INTO itemTags VALUES(?,?,?)", [(I_PAPER, TAG_ML, 0), (I_WEB, TAG_WEB, 0)]
        )

        # notes: standalone + child of the webpage
        c.execute(
            "INSERT INTO itemNotes VALUES(?,?,?,?)",
            (I_NOTE, None, '<div class="zotero-note znv1">standalone note body</div>', None),
        )
        c.execute(
            "INSERT INTO itemNotes VALUES(?,?,?,?)",
            (I_CHILD_NOTE, I_WEB, '<div class="zotero-note znv1">child note body</div>', None),
        )

        # attachments: imported_file pdf, snapshot html, imported_url pdf,
        # linked file (missing), linked url, orphan linked file (missing)
        att_cols = ("itemID,parentItemID,linkMode,contentType,charsetID,path,"
                    "syncState,storageModTime,storageHash,lastProcessedModificationTime,lastRead")
        att_ph = ",".join("?" * 11)

        def attachment(aid, parent, link_mode, ctype, path):
            c.execute(
                f"INSERT INTO itemAttachments({att_cols}) VALUES({att_ph})",
                (aid, parent, link_mode, ctype, None, path, 0, None, None, None, None),
            )

        attachment(A_PAPER, I_PAPER, 0, "application/pdf", "storage:paper.pdf")
        attachment(A_SNAP, I_WEB, 1, "text/html", "storage:snap.html")
        attachment(A_URLPDF, I_URLPAPER, 1, "application/pdf", "storage:urlpaper.pdf")
        attachment(A_MISSING, I_LINKED, 2, "application/pdf", "/nonexistent/missing.pdf")
        attachment(A_LINK, I_LINKURL, 3, "application/pdf", "http://example.com/doc.pdf")
        # orphan: attachment row for the orphan item itself, no parent
        attachment(I_ORPHAN_ATT, None, 2, "text/plain", "/nonexistent/orphan.txt")
        # dangling parent: parentItemID 999 does not exist in items
        attachment(A_DANGLING, 999, 0, "application/pdf", "storage:dangling.pdf")

        # annotations on the paper's PDF
        c.execute(
            "INSERT INTO itemAnnotations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ANN_1, A_PAPER, 1, None, "first highlighted sentence", "first highlighted sentence",
             "a comment", "a comment", "#ff6666", "1", 0, "0,0,1,1", 0),
        )
        c.execute(
            "INSERT INTO itemAnnotations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ANN_2, A_PAPER, 1, None, "second highlighted sentence", "second highlighted sentence",
             None, None, "#ffd400", "2", 1, "0,0,1,1", 0),
        )
        c.execute(
            "INSERT INTO itemAnnotations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (303, A_DANGLING, 1, None, "dangling annotation text", "dangling annotation text",
             None, None, None, None, 0, None, 0),
        )

        # trashed item
        c.execute("INSERT INTO deletedItems VALUES(?,?)", (I_TRASHED, "2024-03-03"))

        conn.commit()
    finally:
        conn.close()

    if storage_dir is not None:
        sd = Path(storage_dir)
        (sd / "ATTBBBBB").mkdir(parents=True, exist_ok=True)
        (sd / "ATTBBBBB" / "paper.pdf").write_bytes(b"%PDF-1.4 fixture paper")
        (sd / "ATTFFFFF").mkdir(parents=True, exist_ok=True)
        (sd / "ATTFFFFF" / "snap.html").write_text(
            "<html><head><title>Fixture snapshot</title></head>"
            "<body><p>snapshot body with zotofixture</p>"
            '<link rel="stylesheet" href="style.css"></body></html>'
        )
        (sd / "ATTFFFFF" / "style.css").write_text("body{color:blue}")
        (sd / "ATTHHHHH").mkdir(parents=True, exist_ok=True)
        (sd / "ATTHHHHH" / "urlpaper.pdf").write_bytes(b"%PDF-1.4 fixture url paper")

    return {k: {name: key for name, key in v.items()} for k, v in KEYS.items()}
