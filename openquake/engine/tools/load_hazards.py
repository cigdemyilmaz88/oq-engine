"""
Load Hazard Calculations dump produced with the
--dump-hazard-calculation option.

The general workflow to load a calculation is the following:

1) Create a "staging" table for each target table (e.g. load_gmf,
load_oq_job, etc.)

2) Use "COPY FROM" statements to populate such table from the content
of the dump

3) INSERT the data SELECTed from the temporary tables INTO the
effective tables in the proper order RETURNING the id of the newly
created rows.

4) If the table is referenced in other tables, we create a temporary
table which maps the old id to the new one. Such table is used in the
SELECT at step 3 to insert the proper foreign key values
"""

import gzip
import logging
import os

from openquake.server.db import models
from django.db.models import fields

log = logging.getLogger()


def quote_unwrap(name):
    if name.startswith("\""):
        return name[1:-1]
    else:
        return name


def load_tablename(original_tablename):
    _schema, tname = map(quote_unwrap, original_tablename.split('.'))
    return "load_%s" % tname


def transfer_data(curs, model, **foreign_keys):
    def model_table(model, load=False):
        original_tablename = "\"%s\"" % model._meta.db_table
        if load:
            return load_tablename(original_tablename)
        else:
            return "{}.{}".format(
                *map(quote_unwrap, original_tablename.split('.')))

    def model_fields(model):
        fs = ", ".join([f.column for f in model._meta.fields
                        if f.column != "id"
                        if not isinstance(f, fields.related.ForeignKey)])
        if fs:
            fs = ", " + fs
        return fs

    conn = curs.connection

    # FIXME(lp). In order to avoid alter the table, we should use a
    # data modifying CTE. I am not using data modifying CTE as I
    # consider it a maintenance nightmare at this moment.
    curs.execute(
        "ALTER TABLE %s ADD load_id INT" % model_table(model))
    args = dict(
        table=model_table(model),
        fields=model_fields(model),
        load_table=model_table(model, load=True),
        fk_fields="", fk_joins="", new_fk_ids="")

    if foreign_keys:
        for fk, id_mapping in foreign_keys.iteritems():
            if fk is not None:
                curs.execute(
                    "CREATE TABLE temp_%s_translation("
                    "%s INT NOT NULL, new_id INT NOT NULL)" % (fk, fk))
                ids = ", ".join(["(%d, %d)" % (old_id, new_id)
                                 for old_id, new_id in id_mapping])
                curs.execute(
                    "INSERT INTO temp_%s_translation VALUES %s" % (fk, ids))

                args['fk_fields'] += ", %s" % fk
                args['fk_joins'] += (
                    "JOIN temp_%s_translation USING(%s) " % (fk, fk))
                args['new_fk_ids'] += ", temp_%s_translation.new_id" % fk

    query = """
INSERT INTO %(table)s (load_id %(fields)s %(fk_fields)s)
SELECT id %(fields)s %(new_fk_ids)s
FROM %(load_table)s AS load
%(fk_joins)s
RETURNING  load_id, %(table)s.id
""" % args

    curs.execute(query)
    old_new_ids = curs.fetchall()
    curs.execute(
        "ALTER TABLE %s DROP load_id" % model_table(model))

    for fk in foreign_keys:
        curs.execute("DROP TABLE temp_%s_translation" % fk)

    conn.commit()

    return old_new_ids


def safe_load(curs, filename, original_tablename):
    """
    Load a postgres table into the database, by skipping the ids
    which are already taken. Assume that the first field of the table
    is an integer id and that gzfile.name has the form
    '/some/path/tablename.csv.gz' The file is loaded in blocks to
    avoid memory issues.

    :param curs: a psycopg2 cursor
    :param filename: the path to the csv dump
    :param str tablename: full table name
    """
    # keep in memory the already taken ids
    conn = curs.connection
    tablename = load_tablename(original_tablename)
    try:
        curs.execute("DROP TABLE IF EXISTS %s" % tablename)
        curs.execute(
            "CREATE TABLE %s AS SELECT * FROM %s WHERE 0 = 1" % (
                tablename, original_tablename))
        curs.copy_expert(
            """COPY %s FROM stdin
               WITH (FORMAT 'csv', HEADER true, ENCODING 'utf8')""" %
            tablename, gzip.GzipFile(os.path.abspath(filename)))
    except Exception as e:
        conn.rollback()
        log.error(str(e))
        raise
    else:
        conn.commit()


def hazard_load(conn, directory):
    """
    Import a tar file generated by the HazardDumper.

    :param conn: the psycopg2 connection to the db
    :param directory: the pathname to the directory with the .gz files
    """
    filenames = os.path.join(directory, 'FILENAMES.txt')
    curs = conn.cursor()

    created = []
    for line in open(filenames):
        fname = line.rstrip()
        tname = fname[:-7]  # strip .csv.gz

        fullname = os.path.join(directory, fname)
        log.info('Importing %s...', fname)
        created.append(tname)
        safe_load(curs, fullname, tname)

    job_ids = transfer_data(curs, models.OqJob)
    sm_ids = transfer_data(
        curs, models.LtSourceModel, hazard_calculation_id=job_ids)
    lt_ids = transfer_data(
        curs, models.LtRealization, lt_model_id=sm_ids)
    transfer_data(
        curs, models.HazardSite, hazard_calculation_id=job_ids)
    out_ids = transfer_data(
        curs, models.Output, oq_job_id=job_ids)
    ses_collection_ids = transfer_data(
        curs, models.SESCollection,
        output_id=out_ids, lt_realization_id=lt_ids)
    rup_ids = transfer_data(
        curs, models.ProbabilisticRupture,
        ses_collection_id=ses_collection_ids)
    transfer_data(curs, models.SESRupture, rupture_id=rup_ids)

    curs = conn.cursor()
    try:
        for tname in reversed(created):
            query = "DROP TABLE %s" % load_tablename(tname)
            curs.execute(query)
            log.info("Dropped %s" % load_tablename(tname))
    except Exception:
        conn.rollback()
    else:
        conn.commit()
    log.info('Loaded %s', directory)
    return [new_id for _, new_id in hc_ids]
