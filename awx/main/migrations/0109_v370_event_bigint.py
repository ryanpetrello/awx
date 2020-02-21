# Generated by Django 2.2.8 on 2020-02-21 16:31

from django.db import migrations, models, connection


def migrate_event_data(apps, schema_editor):
    # see: https://github.com/ansible/awx/issues/6010
    #
    # the goal of this function is to end with event tables (e.g., main_jobevent)
    # that have a bigint primary key (because the old usage of an integer
    # numeric isn't enough, as its range is about 2.1B, see:
    # https://www.postgresql.org/docs/9.1/datatype-numeric.html)

    # unfortunately, we can't do this with a simple ALTER TABLE, because
    # for tables with hundreds of millions or billions of rows, the ALTER TABLE
    # can take *hours* on modest hardware.
    #
    # the approach in this migration means that post-migration, event data will
    # *not* immediately show up, but will be repopulated over time progressively
    # the trade-off here is not having to wait hours for the full data migration
    # before you can start and run AWX again (including new playbook runs)
    for tblname in (
        'main_jobevent', 'main_inventoryupdateevent',
        'main_projectupdateevent', 'main_adhoccommandevent',
        'main_systemjobevent'
    ):
        with connection.cursor() as cursor:
            # rename the current event table
            cursor.execute(
                f'ALTER TABLE {tblname} RENAME TO _old_{tblname};'
            )
            # create a *new* table with the same schema
            cursor.execute(
                f'CREATE TABLE {tblname} (LIKE _old_{tblname} INCLUDING ALL);'
            )
            # alter the *new* table so that the primary key is a big int
            cursor.execute(
                f'ALTER TABLE {tblname} ALTER COLUMN id TYPE bigint USING id::bigint;'
            )

            # recreate counter for the new table's primary key to
            # start where the *old* table left off (we have to do this because the
            # counter changed from an int to a bigint)
            cursor.execute(f'DROP SEQUENCE IF EXISTS "{tblname}_id_seq" CASCADE;')
            cursor.execute(f'CREATE SEQUENCE "{tblname}_id_seq";')
            cursor.execute(
                f'ALTER TABLE "{tblname}" ALTER COLUMN "id" '
                f"SET DEFAULT nextval('{tblname}_id_seq');"
            )
            cursor.execute(
                f"SELECT setval('{tblname}_id_seq', (SELECT MAX(id) FROM _old_{tblname}), true);"
            )


class FakeAlterField(migrations.AlterField):

    def database_forwards(self, *args):
        # this is intentionally left blank, because we're
        # going to accomplish the migration with some custom raw SQL
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0108_v370_unifiedjob_dependencies_processed'),
    ]

    operations = [
        migrations.RunPython(migrate_event_data),
        FakeAlterField(
            model_name='adhoccommandevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        FakeAlterField(
            model_name='inventoryupdateevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        FakeAlterField(
            model_name='jobevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        FakeAlterField(
            model_name='projectupdateevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        FakeAlterField(
            model_name='systemjobevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
