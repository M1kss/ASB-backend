import gzip
import os
from sqlalchemy import tuple_
import re
from datetime import datetime
from ASB_app import logger, executor
from ASB_app.constants import possible_tf_asbs_rs, possible_cl_asbs_rs, possible_cl_candidates_rs, possible_all_asbs_rs, \
    possible_all_candidates_rs, possible_tf_candidates_rs, possible_tf_asbs, possible_tf_candidates, possible_cl_asbs, \
    possible_cl_candidates, possible_all_asbs, possible_all_candidates, chromosomes
from ASB_app.service import ananastra_service
from ASB_app.utils import pack, process_row, group_concat_distinct_sep
from sqlalchemy.orm import aliased
import numpy as np
import pandas as pd
from scipy.stats import fisher_exact
from statsmodels.stats.multitest import multipletests

from ASB_app.models import CandidateSNP

from ASB_app.releases import current_release

session = current_release.session
db = current_release.db

TranscriptionFactor, TranscriptionFactorSNP, CellLine, CellLineSNP, \
SNP, ExpSNP, Phenotype, PhenotypeSNPCorrespondence, Gene, Experiment = \
    current_release.TranscriptionFactor, current_release.TranscriptionFactorSNP, current_release.CellLine, current_release.CellLineSNP, \
    current_release.SNP, current_release.ExpSNP, current_release.Phenotype, current_release.PhenotypeSNPCorrespondence, current_release.Gene, current_release.Experiment


class ConvError(ValueError):
    pass


def convert_rs_to_int(rs_str):
    rs_str = rs_str.strip()
    if not re.match(r'^rs\d+$', rs_str):
        raise ConvError(rs_str)
    return int(rs_str[2:])


def get_tf_query(rs_ids):
    grasp = aliased(Phenotype, name='grasp')
    ebi = aliased(Phenotype, name='ebi')
    clinvar = aliased(Phenotype, name='clinvar')
    finemapping = aliased(Phenotype, name='finemapping')
    qtl = aliased(Phenotype, name='qtl')
    phewas = aliased(Phenotype, name='phewas')

    return session.query(
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.chromosome)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.position)),
        db.func.group_concat(db.func.distinct(SNP.rs_id)),
        db.func.group_concat(db.func.distinct(SNP.ref)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.alt)),
        db.func.group_concat(db.func.distinct(SNP.context)),
        db.func.group_concat(db.func.distinct(TranscriptionFactor.name)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.peak_calls)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.mean_bad)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.log_p_value_ref)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.log_p_value_alt)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.es_ref)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.es_alt)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_log_p_ref)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_log_p_alt)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_log_2_fc)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_position)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_orientation)),
        db.func.group_concat(db.func.distinct(TranscriptionFactorSNP.motif_concordance)),
        group_concat_distinct_sep(CellLine.name, ', '),
        group_concat_distinct_sep(qtl.phenotype_name, ', '),
        group_concat_distinct_sep(ebi.phenotype_name, ', '),
        group_concat_distinct_sep(phewas.phenotype_name, ', '),
        group_concat_distinct_sep(finemapping.phenotype_name, ', '),
        group_concat_distinct_sep(grasp.phenotype_name, ', '),
        group_concat_distinct_sep(clinvar.phenotype_name, ', '),
        group_concat_distinct_sep(Gene.gene_name, ', '),
    ).join(
        SNP,
        TranscriptionFactorSNP.snp
    ).filter(
        SNP.rs_id.in_(rs_ids)
    ).join(
        TranscriptionFactor,
        TranscriptionFactorSNP.transcription_factor
    ).join(
        PhenotypeSNPCorrespondence,
        (SNP.chromosome == PhenotypeSNPCorrespondence.chromosome) &
        (SNP.position == PhenotypeSNPCorrespondence.position) &
        (SNP.alt == PhenotypeSNPCorrespondence.alt),
        isouter=True
    ).join(
        ExpSNP,
        TranscriptionFactorSNP.exp_snps
    ).filter(
        (ExpSNP.p_value_ref - ExpSNP.p_value_alt) * (TranscriptionFactorSNP.log_p_value_alt - TranscriptionFactorSNP.log_p_value_ref) > 0
    ).join(
        Experiment,
        ExpSNP.experiment,
    ).join(
        CellLine,
        Experiment.cell_line,
    ).join(
        Gene,
        SNP.target_genes,
        isouter=True
    ).join(
        qtl,
        (PhenotypeSNPCorrespondence.phenotype_id == qtl.phenotype_id) &
        (qtl.db_name == 'QTL'),
        isouter=True
    ).join(
        ebi,
        (PhenotypeSNPCorrespondence.phenotype_id == ebi.phenotype_id) &
        (ebi.db_name == 'ebi'),
        isouter=True
    ).join(
        phewas,
        (PhenotypeSNPCorrespondence.phenotype_id == phewas.phenotype_id) &
        (phewas.db_name == 'phewas'),
        isouter=True
    ).join(
        finemapping,
        (PhenotypeSNPCorrespondence.phenotype_id == finemapping.phenotype_id) &
        (finemapping.db_name == 'finemapping'),
        isouter=True
    ).join(
        grasp,
        (PhenotypeSNPCorrespondence.phenotype_id == grasp.phenotype_id) &
        (grasp.db_name == 'grasp'),
        isouter=True
    ).join(
        clinvar,
        (PhenotypeSNPCorrespondence.phenotype_id == clinvar.phenotype_id) &
        (clinvar.db_name == 'clinvar'),
        isouter=True
    ).group_by(TranscriptionFactorSNP.tf_snp_id)


def get_cl_query(rs_ids):
    grasp = aliased(Phenotype, name='grasp')
    ebi = aliased(Phenotype, name='ebi')
    clinvar = aliased(Phenotype, name='clinvar')
    finemapping = aliased(Phenotype, name='finemapping')
    qtl = aliased(Phenotype, name='qtl')
    phewas = aliased(Phenotype, name='phewas')

    return session.query(
        db.func.group_concat(db.func.distinct(CellLineSNP.chromosome)),
        db.func.group_concat(db.func.distinct(CellLineSNP.position)),
        db.func.group_concat(db.func.distinct(SNP.rs_id)),
        db.func.group_concat(db.func.distinct(SNP.ref)),
        db.func.group_concat(db.func.distinct(CellLineSNP.alt)),
        db.func.group_concat(db.func.distinct(SNP.context)),
        db.func.group_concat(db.func.distinct(CellLine.name)),
        db.func.group_concat(db.func.distinct(CellLineSNP.peak_calls)),
        db.func.group_concat(db.func.distinct(CellLineSNP.mean_bad)),
        db.func.group_concat(db.func.distinct(CellLineSNP.log_p_value_ref)),
        db.func.group_concat(db.func.distinct(CellLineSNP.log_p_value_alt)),
        db.func.group_concat(db.func.distinct(CellLineSNP.es_ref)),
        db.func.group_concat(db.func.distinct(CellLineSNP.es_alt)),
        group_concat_distinct_sep(TranscriptionFactor.name, ', '),
        group_concat_distinct_sep(qtl.phenotype_name, ', '),
        group_concat_distinct_sep(ebi.phenotype_name, ', '),
        group_concat_distinct_sep(phewas.phenotype_name, ', '),
        group_concat_distinct_sep(finemapping.phenotype_name, ', '),
        group_concat_distinct_sep(grasp.phenotype_name, ', '),
        group_concat_distinct_sep(clinvar.phenotype_name, ', '),
        group_concat_distinct_sep(Gene.gene_name, ', '),
    ).join(
        SNP,
        CellLineSNP.snp
    ).filter(
        SNP.rs_id.in_(rs_ids)
    ).join(
        CellLine,
        CellLineSNP.cell_line
    ).join(
        PhenotypeSNPCorrespondence,
        (SNP.chromosome == PhenotypeSNPCorrespondence.chromosome) &
        (SNP.position == PhenotypeSNPCorrespondence.position) &
        (SNP.alt == PhenotypeSNPCorrespondence.alt),
        isouter=True
    ).join(
        ExpSNP,
        CellLineSNP.exp_snps
    ).filter(
        (ExpSNP.p_value_ref - ExpSNP.p_value_alt) * (CellLineSNP.log_p_value_alt - CellLineSNP.log_p_value_ref) > 0
    ).join(
        Experiment,
        ExpSNP.experiment,
    ).join(
        TranscriptionFactor,
        Experiment.transcription_factor,
    ).join(
        Gene,
        SNP.target_genes,
        isouter=True
    ).join(
        qtl,
        (PhenotypeSNPCorrespondence.phenotype_id == qtl.phenotype_id) &
        (qtl.db_name == 'QTL'),
        isouter=True
    ).join(
        ebi,
        (PhenotypeSNPCorrespondence.phenotype_id == ebi.phenotype_id) &
        (ebi.db_name == 'ebi'),
        isouter=True
    ).join(
        phewas,
        (PhenotypeSNPCorrespondence.phenotype_id == phewas.phenotype_id) &
        (phewas.db_name == 'phewas'),
        isouter=True
    ).join(
        finemapping,
        (PhenotypeSNPCorrespondence.phenotype_id == finemapping.phenotype_id) &
        (finemapping.db_name == 'finemapping'),
        isouter=True
    ).join(
        grasp,
        (PhenotypeSNPCorrespondence.phenotype_id == grasp.phenotype_id) &
        (grasp.db_name == 'grasp'),
        isouter=True
    ).join(
        clinvar,
        (PhenotypeSNPCorrespondence.phenotype_id == clinvar.phenotype_id) &
        (clinvar.db_name == 'clinvar'),
        isouter=True
    ).group_by(CellLineSNP.cl_snp_id)


def get_tf_asbs(rs_ids, mode='all'):
    q = TranscriptionFactorSNP.query.join(SNP, TranscriptionFactorSNP.snp).filter(
        SNP.rs_id.in_(rs_ids),
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def get_cl_asbs(rs_ids, mode='all'):
    q = CellLineSNP.query.join(SNP, CellLineSNP.snp).filter(
        SNP.rs_id.in_(rs_ids),
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def get_all_asbs(rs_ids, mode='all'):
    q = SNP.query.filter(
        SNP.rs_id.in_(rs_ids)
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def get_tf_candidates(rs_ids, mode='all'):
    q = CandidateSNP.query.filter(
        CandidateSNP.rs_id.in_(rs_ids),
        CandidateSNP.ag_level == 'TF',
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def get_cl_candidates(rs_ids, mode='all'):
    q = CandidateSNP.query.filter(
        CandidateSNP.rs_id.in_(rs_ids),
        CandidateSNP.ag_level == 'CL',
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def get_all_candidates(rs_ids, mode='all'):
    q = CandidateSNP.query.filter(
        CandidateSNP.rs_id.in_(rs_ids)
    )
    if mode == 'count':
        return q.count()
    elif mode == 'all':
        return q.all()


def divide_chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]


def divide_query(get_query, values, chunk_size=900):
    for chunk in divide_chunks(values, chunk_size):
        yield get_query(chunk)


def get_alleles(df):
    assert len(set(df['REF'].tolist())) == 1
    ref = df['REF'].tolist()[0]
    alts = list(set(df['ALT'].tolist()))
    return '/'.join([ref] + alts)


def get_preferences(df):
    ref = len(df[df['LOG10_FDR_REF'] > df['LOG10_FDR_ALT']].index) > 0
    alt = len(df[df['LOG10_FDR_ALT'] > df['LOG10_FDR_REF']].index) > 0
    assert ref or alt
    if ref and alt:
        return 'Both'
    elif ref:
        return 'Ref'
    else:
        return 'Alt'


def modify_counts(asb_data, counts=None, top=False):
    if top:
        if not counts:
            return []
        tuples = []
        data_by_name = {data['name']: data for data in asb_data}
        for count in counts:
            data = data_by_name[count['name']]
            tuples.append((count, data))
        counts_list, _ = (list(a) for a in zip(*sorted(tuples, key=lambda x: (x[0]['count'], x[1]['odds']), reverse=True)))
        if len(counts_list) > 7:
            counts_list = counts_list[:6] + [{'name': 'Other', 'count': sum(x['count'] for x in counts_list[6:])}]
    else:
        counts_list = sorted(asb_data, key=lambda x: (x['asbs'], x['odds']), reverse=True)
        if len(counts_list) > 7:
            counts_list = [{'name': x['name'], 'count': x['asbs']} for x in counts_list[:6]] + [{'name': 'Other', 'count': sum(x['asbs'] for x in counts_list[6:])}]
        else:
            counts_list = [{'name': x['name'], 'count': x['asbs']} for x in counts_list]
    return counts_list


def update_ticket_status(ticket, status):
    meta_info = dict(ticket.meta_info)
    meta_info.update({
        'status_details': status,
        'last_status_update_at': str(datetime.now()),
    })
    ticket.meta_info = meta_info
    session.commit()


def get_rs_ids_by_chr_pos_query(chromosome, tuples, candidates=False):
    if candidates:
        return CandidateSNP.query.filter(CandidateSNP.chromosome == chromosome, tuple_(CandidateSNP.position, CandidateSNP.ref, CandidateSNP.alt).in_(tuples)).all()
    else:
        return SNP.query.filter(SNP.chromosome == chromosome, tuple_(SNP.position, SNP.ref, SNP.alt).in_(tuples)).all()


def get_rs_ids_from_vcf(data):
    snps = []
    for chr in data[0].unique():
        print(chr)
        if chr not in chromosomes:
            if 'chr' + str(chr) in chromosomes:
                chr = 'chr' + chr
            else:
                continue
                raise ConvError('chromosome: {}'.format(chr))
        try:
            tuples = [(int(position), ref.upper(), alt.upper()) for index, (position, ref, alt) in data.loc[data[0] == chr, [1, 3, 4]].iterrows()]
        except ValueError as e:
            raise ConvError('position: {}'.format(e.args[0]))
        for snps_chunk in divide_query(lambda poss: get_rs_ids_by_chr_pos_query(chr, poss), tuples, chunk_size=300):
            snps += snps_chunk
        for snps_chunk in divide_query(lambda poss: get_rs_ids_by_chr_pos_query(chr, poss, candidates=True), tuples, chunk_size=300):
            snps += snps_chunk
    return list(set(x.rs_id for x in snps))


def get_snps_from_interval(interval_str):
    interval_str = interval_str.strip()
    match = re.match(r'^(chr)?(\d|1\d|2[0-2]|X|Y):([1-9]\d*)-([1-9]\d*)$', interval_str)
    if match:
        chr, start, end = match.groups()[1:]
        chr = 'chr' + chr
        start = int(start)
        end = int(end)
        return list(set(x for (x, ) in session.query(SNP.rs_id).filter(
            SNP.chromosome == chr,
            SNP.position.between(start, end)
        )) | set(x.rs_id for x in CandidateSNP.query.filter(
            CandidateSNP.chromosome == chr,
            CandidateSNP.position.between(start, end)
        )))
    else:
        raise ConvError(interval_str)


def marshal_inf(odds):
    if np.isinf(odds):
        return 'infinity'
    elif np.isnan(odds):
        return None
    else:
        return str(odds)


def marshal_logp(p):
    if p == 0:
        return 'infinity'
    if np.isnan(p):
        return None
    if p is None:
        return p
    return str(-np.log10(p))


def marshall_data(asb_data):
    return [{k: marshal_inf(v) if k in ('log10_p_value', 'log10_fdr', 'odds') else v for (k, v) in elem.items()} for elem in asb_data]


@executor.job
def process_snp_file(ticket_id, annotate_tf=True, annotate_cl=True):
    processing_start_time = datetime.now()
    input_file_name = ananastra_service.get_path_by_ticket_id(ticket_id)
    ticket = ananastra_service.get_ticket(ticket_id)
    change_status_on_fail = False
    try:
        len_items = None
        ticket.status = 'Processing'
        ticket.meta_info = {'processing_started_at': str(datetime.now())}
        update_ticket_status(ticket, 'Processing started')
        try:
            with gzip.open(input_file_name, 'rt') as f:
                data = pd.read_table(f, sep='\t', header=None, encoding='utf-8', dtype=str, comment='#')
        except:
            try:
                data = pd.read_table(input_file_name, sep='\t', header=None, encoding='utf-8', dtype=str, comment='#')
            except:
                update_ticket_status(ticket, 'Processing failed: the file must be a valid utf-8 text file with a single SNP rs-ID on each line or a single line with genomic interval or a valid .vcf(.gz) file')
                raise ConvError
        if len(data.columns) != 1:
            len_items = len(data.index)
            try:
                rs_ids = get_rs_ids_from_vcf(data)
            except ConvError as e:
                update_ticket_status(ticket, 'Processing failed: the file must contain a single SNP rs-ID on each line or a single line with genomic interval or be a valid vcf file, invalid {}'.format(e.args[0]))
                raise ConvError
            except:
                change_status_on_fail = True
                raise
        else:
            rs_ids = None
            if len(data.index) == 1:
                try:
                    rs_ids = get_snps_from_interval(data[0][0])
                except ConvError:
                    pass
                except:
                    change_status_on_fail = True
                    raise
            if rs_ids is None:
                try:
                    rs_ids = data[0].apply(convert_rs_to_int).unique().tolist()
                except ConvError as e:
                    if len(data.index) > 1:
                        update_ticket_status(ticket, 'Processing failed, invalid rs id: "{}"'.format(e.args[0]))
                    else:
                        update_ticket_status(ticket, 'Processing failed, invalid rs id or genomic interval: "{}"'.format(e.args[0]))
                    raise ConvError
                except:
                    change_status_on_fail = True
                    raise

        if len_items is None:
            len_items = len(rs_ids)

        if len_items > 10000:
            update_ticket_status(ticket, 'Processing failed, maximum number of itmes exceeds 10000')
            raise ConvError

        change_status_on_fail = True

        common_header_1 = ['CHROMOSOME', 'POSITION', 'RS_ID', 'REF', 'ALT', 'SEQUENCE']
        common_header_2 = ['PEAK_CALLS', 'MEAN_BAD', 'LOG10_FDR_REF', 'LOG10_FDR_ALT',
                           'EFFECT_SIZE_REF', 'EFFECT_SIZE_ALT']
        common_header_3 = ['GTEX_EQTL', 'EBI', 'PHEWAS', 'FINEMAPPING', 'GRASP', 'CLINVAR', 'GTEX_EQTL_TARGET_GENES']
        cl_header = common_header_1 + ['CELL_TYPE'] + common_header_2 + ['SUPPORTING_TFS'] + common_header_3
        tf_header = common_header_1 + ['TRANSCRIPTION_FACTOR'] + common_header_2 + \
                    ['MOTIF_LOG_P_REF', 'MOTIF_LOG_P_ALT', 'MOTIF_LOG2_FC', 'MOTIF_POSITION',
                     'MOTIF_ORIENTATION', 'MOTIF_CONCORDANCE', 'SUPPORTING_CELL_TYPES'] + common_header_3

        tf_asb_counts = {}
        tf_sum_counts = {}
        conc_asbs = []
        logger.info('Ticket {}: processing started'.format(ticket_id))
        update_ticket_status(ticket, 'Searching for ASBs of transcription factors (TF-ASBs)')

        if annotate_tf:
            ananastra_service.create_processed_path(ticket_id, 'tf')
            tf_path = ananastra_service.get_path_by_ticket_id(ticket_id, path_type='tf', ext='.tsv')

            with open(tf_path, 'w', encoding='utf-8') as out:
                out.write(pack(tf_header))

            for q_tf in divide_query(get_tf_query, rs_ids):
                with open(tf_path, 'a', encoding='utf-8') as out:
                    for tup in q_tf:
                        tf_name = tup[6]
                        rs_id = tup[2]
                        alt = tup[4]
                        conc = tup[18]
                        tf_asb_counts.setdefault(tf_name, {
                            'name': tf_name,
                            'count': 0
                        })['count'] += 1
                        if conc not in ('No Hit', None):
                            conc_asbs.append({
                                    'tf_name': tf_name,
                                    'rs_id': rs_id,
                                    'alt': alt,
                                    'concordance': conc,
                                })

                        out.write(pack(process_row(tup, 'TF', tf_header)))

            logger.info('Ticket {}: tf done'.format(ticket_id))
            update_ticket_status(ticket, 'Aggregating TF-ASBs information')

            ananastra_service.create_processed_path(ticket_id, 'tf_sum')

            tf_table = pd.read_table(tf_path, encoding='utf-8', na_values=['None', 'NaN', 'nan'])
            tf_table['LOG10_TOP_FDR'] = tf_table[['LOG10_FDR_REF', 'LOG10_FDR_ALT']].max(axis=1)
            tf_table['IS_EQTL'] = tf_table['GTEX_EQTL_TARGET_GENES'].apply(lambda x: False if pd.isna(x) else True)
            idx = tf_table.groupby(['RS_ID', 'ALT'])['LOG10_TOP_FDR'].transform(max) == tf_table['LOG10_TOP_FDR']
            tf_sum_table = tf_table.loc[idx].copy()
            if len(idx) > 0:
                tf_sum_table['TOP_EFFECT_SIZE'] = tf_sum_table.apply(lambda row: row['EFFECT_SIZE_REF'] if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else row['EFFECT_SIZE_ALT'], axis=1)
                tf_sum_table['PREFERRED_ALLELE'] = tf_sum_table.apply(lambda row: 'Ref ({})'.format(row['REF']) if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else 'Alt ({})'.format(row['ALT']), axis=1)
                tf_sum_table['MINOR_ALLELE'] = tf_sum_table.apply(lambda row: 'Alt ({})'.format(row['ALT']) if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else 'Ref ({})'.format(row['REF']), axis=1)
                tf_table.drop(columns=['LOG10_TOP_FDR'], inplace=True)
                tf_sum_table.drop(columns=['LOG10_FDR_REF', 'LOG10_FDR_ALT', 'EFFECT_SIZE_REF', 'EFFECT_SIZE_ALT'], inplace=True)
                tf_sum_table['IS_EQTL'] = tf_sum_table['GTEX_EQTL_TARGET_GENES'].apply(lambda x: False if pd.isna(x) else True)
                tf_sum_table['ALLELES'] = tf_sum_table.apply(lambda row: get_alleles(tf_table.loc[tf_table['RS_ID'] == row['RS_ID'], ['REF', 'ALT']]), axis=1)
                tf_sum_table['TF_BINDING_PREFERENCES'] = tf_sum_table.apply(lambda row: get_preferences(tf_table.loc[tf_table['RS_ID'] == row['RS_ID'], ['LOG10_FDR_REF', 'LOG10_FDR_ALT']]), axis=1)
                tf_sum_table.drop(columns=['REF', 'ALT'])
                tf_sum_table.to_csv(ananastra_service.get_path_by_ticket_id(ticket_id, 'tf_sum'), sep='\t', index=False)
                tf_table.to_csv(tf_path, sep='\t', index=False)
                tf_sum_counts = tf_sum_table['TRANSCRIPTION_FACTOR'].value_counts().to_dict()
            else:
                tf_sum_table.to_csv(ananastra_service.get_path_by_ticket_id(ticket_id, 'tf_sum'), sep='\t', index=False)

            logger.info('Ticket {}: tf_sum done'.format(ticket_id))
            update_ticket_status(ticket, 'Searching for cell type-ASBs')

        cl_asb_counts = {}
        cl_sum_counts = {}
        if annotate_cl:
            ananastra_service.create_processed_path(ticket_id, 'cl')
            cl_path = ananastra_service.get_path_by_ticket_id(ticket_id, path_type='cl', ext='.tsv')

            with open(cl_path, 'w', encoding='utf-8') as out:
                out.write(pack(cl_header))

            for q_cl in divide_query(get_cl_query, rs_ids):
                with open(cl_path, 'a', encoding='utf-8') as out:
                    for tup in q_cl:
                        cl_name = tup[6]
                        cl_asb_counts.setdefault(cl_name, {
                            'name': cl_name,
                            'count': 0
                        })['count'] += 1

                        out.write(pack(process_row(tup, 'CL', cl_header)))

            logger.info('Ticket {}: cl done'.format(ticket_id))
            update_ticket_status(ticket, 'Aggregating CL-ASBs information')

            ananastra_service.create_processed_path(ticket_id, 'cl_sum')

            cl_table = pd.read_table(cl_path, encoding='utf-8', na_values=['None', 'NaN', 'nan'])
            cl_table['LOG10_TOP_FDR'] = cl_table[['LOG10_FDR_REF', 'LOG10_FDR_ALT']].max(axis=1)
            cl_table['IS_EQTL'] = cl_table['GTEX_EQTL_TARGET_GENES'].apply(lambda x: False if pd.isna(x) else True)
            idx = cl_table.groupby(['RS_ID', 'ALT'])['LOG10_TOP_FDR'].transform(max) == cl_table['LOG10_TOP_FDR']
            cl_sum_table = cl_table.loc[idx].copy()
            if len(idx) > 0:
                cl_sum_table['TOP_EFFECT_SIZE'] = cl_sum_table.apply(lambda row: row['EFFECT_SIZE_REF'] if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else row['EFFECT_SIZE_ALT'], axis=1)
                cl_sum_table['PREFERRED_ALLELE'] = cl_sum_table.apply(lambda row: 'Ref ({})'.format(row['REF']) if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else 'Alt ({})'.format(row['ALT']), axis=1)
                cl_sum_table['MINOR_ALLELE'] = cl_sum_table.apply(lambda row: 'Alt ({})'.format(row['ALT']) if row['LOG10_FDR_REF'] >= row['LOG10_FDR_ALT'] else 'Ref ({})'.format(row['REF']), axis=1)
                cl_table.drop(columns=['LOG10_TOP_FDR'], inplace=True)
                cl_sum_table.drop(columns=['LOG10_FDR_REF', 'LOG10_FDR_ALT', 'EFFECT_SIZE_REF', 'EFFECT_SIZE_ALT'], inplace=True)
                cl_sum_table['ALLELES'] = cl_sum_table.apply(lambda row: get_alleles(cl_table.loc[cl_table['RS_ID'] == row['RS_ID'], ['REF', 'ALT']]), axis=1)
                cl_sum_table['TF_BINDING_PREFERENCES'] = cl_sum_table.apply(lambda row: get_preferences(cl_table.loc[cl_table['RS_ID'] == row['RS_ID'], ['LOG10_FDR_REF', 'LOG10_FDR_ALT']]), axis=1)
                cl_sum_table.drop(columns=['REF', 'ALT'])
                cl_sum_table.to_csv(ananastra_service.get_path_by_ticket_id(ticket_id, 'cl_sum'), sep='\t', index=False)
                cl_table.to_csv(cl_path, sep='\t', index=False)
                cl_sum_counts = cl_sum_table['CELL_TYPE'].value_counts().to_dict()
            else:
                cl_sum_table.to_csv(ananastra_service.get_path_by_ticket_id(ticket_id, 'cl_sum'), sep='\t', index=False)


            logger.info('Ticket {}: cl_sum done'.format(ticket_id))
            update_ticket_status(ticket, 'Checking the control data of candidate but non-significant ASBs (non-ASBs)')

        tf_sum_counts = [{'name': key, 'count': value} for key, value in tf_sum_counts.items()]
        cl_sum_counts = [{'name': key, 'count': value} for key, value in cl_sum_counts.items()]

        all_rs = len_items
        tf_asbs_list = [x for query in divide_query(get_tf_asbs, rs_ids) for x in query]
        tf_asbs = len(tf_asbs_list)
        tf_asbs_rs = len(set(x.snp.rs_id for x in tf_asbs_list))
        cl_asbs_list = [x for query in divide_query(get_cl_asbs, rs_ids) for x in query]
        cl_asbs = len(cl_asbs_list)
        cl_asbs_rs = len(set(x.snp.rs_id for x in cl_asbs_list))
        all_asbs_list = [x for query in divide_query(get_all_asbs, rs_ids) for x in query]
        all_asbs = tf_asbs + cl_asbs
        all_asbs_rs = len(set(x.rs_id for x in all_asbs_list))

        logger.info('Ticket {}: query count asb done'.format(ticket_id))
        update_ticket_status(ticket, 'Checking the control data of candidate but non-significant ASBs (non-ASBs)')

        tf_candidates_list = [x for query in divide_query(get_tf_candidates, rs_ids) for x in query]
        tf_candidates = len(tf_candidates_list)
        tf_candidates_rs = len(set(x.rs_id for x in tf_candidates_list))
        cl_candidates_list = [x for query in divide_query(get_cl_candidates, rs_ids) for x in query]
        cl_candidates = len(cl_candidates_list)
        cl_candidates_rs = len(set(x.rs_id for x in cl_candidates_list))
        all_candidates_list = [x for query in divide_query(get_all_candidates, rs_ids) for x in query]
        all_candidates = tf_candidates + cl_candidates
        all_candidates_rs = len(set(x.rs_id for x in all_candidates_list))

        logger.info('Ticket {}: query count candidates done'.format(ticket_id))
        update_ticket_status(ticket, 'Performing statistical analysis')

        tf_odds_rs, tf_p_rs = fisher_exact(((tf_asbs_rs, tf_candidates_rs-tf_asbs_rs), (possible_tf_asbs_rs, possible_tf_candidates_rs-possible_tf_asbs_rs)), alternative='greater')
        tf_odds, tf_p = fisher_exact(((tf_asbs, tf_candidates-tf_asbs), (possible_tf_asbs, possible_tf_candidates-possible_tf_asbs)), alternative='greater')

        cl_odds_rs, cl_p_rs = fisher_exact(((cl_asbs_rs, cl_candidates_rs-cl_asbs_rs), (possible_cl_asbs_rs, possible_cl_candidates_rs-possible_cl_asbs_rs)), alternative='greater')
        cl_odds, cl_p = fisher_exact(((cl_asbs, cl_candidates-cl_asbs), (possible_cl_asbs, possible_cl_candidates-possible_cl_asbs)), alternative='greater')

        all_odds_rs, all_p_rs = fisher_exact(((all_asbs_rs, all_candidates_rs-all_asbs_rs), (possible_all_asbs_rs, possible_all_candidates_rs-possible_all_asbs_rs)), alternative='greater')
        all_odds, all_p = fisher_exact(((all_asbs, all_candidates-all_asbs), (possible_all_asbs, possible_all_candidates-possible_all_asbs)), alternative='greater')

        logger.info('Ticket {}: tests done'.format(ticket_id))
        update_ticket_status(ticket, 'Testing the enrichment of ASBs of individual TFs')

        tf_p_list = []
        tf_asb_data = []
        for tf in tf_asb_counts.keys():
            tf_id = TranscriptionFactor.query.filter_by(name=tf).one().tf_id
            asbs = tf_asb_counts[tf]['count']
            asbs_rs = len(set(x.snp.rs_id for x in tf_asbs_list if x.tf_id == tf_id))
            candidates = len([cand for cand in tf_candidates_list if cand.ag_id == tf_id])
            candidates_rs = len(set(cand.rs_id for cand in tf_candidates_list if cand.ag_id == tf_id))
            odds, p = fisher_exact(((asbs_rs, candidates_rs-asbs_rs), (possible_tf_asbs_rs, possible_tf_candidates_rs-possible_tf_asbs_rs)), alternative='greater')
            tf_p_list.append(p)
            tf_asb_data.append({
                'name': tf,
                'asbs': asbs,
                'asbs_rs': asbs_rs,
                'candidates': candidates,
                'candidates_rs': candidates_rs,
                'odds': odds,
                'log10_p_value': -np.log10(p),
                'log10_fdr': 0,
            })
        if len(tf_p_list) == 0:
            tf_fdr = []
        else:
            _, tf_fdr, _, _ = multipletests(tf_p_list, alpha=0.05, method='fdr_bh')
            for sig, fdr in zip(tf_asb_data, tf_fdr):
                sig['log10_fdr'] = np.nan if np.isnan(fdr) else -np.log10(fdr)

        logger.info('Ticket {}: tf tests done'.format(ticket_id))
        update_ticket_status(ticket, 'Testing the enrichment of ASBs of individual cell types')

        cl_p_list = []
        cl_asb_data = []
        for cl in cl_asb_counts.keys():
            cl_id = CellLine.query.filter_by(name=cl).one().cl_id
            asbs = cl_asb_counts[cl]['count']
            asbs_rs = len(set(x.snp.rs_id for x in cl_asbs_list if x.cl_id == cl_id))
            candidates = len([cand for cand in cl_candidates_list if cand.ag_id == cl_id])
            candidates_rs = len(set(cand.rs_id for cand in cl_candidates_list if cand.ag_id == cl_id))
            odds, p = fisher_exact(((asbs_rs, candidates_rs-asbs_rs), (possible_cl_asbs_rs, possible_cl_candidates_rs-possible_cl_asbs_rs)), alternative='greater')
            cl_p_list.append(p)
            cl_asb_data.append({
                'name': cl,
                'asbs': asbs,
                'asbs_rs': asbs_rs,
                'candidates': candidates,
                'candidates_rs': candidates_rs,
                'odds': odds,
                'log10_p_value': -np.log10(p),
                'log10_fdr': 0,
            })
        if len(cl_p_list) == 0:
            cl_fdr = []
        else:
            _, cl_fdr, _, _ = multipletests(cl_p_list, alpha=0.05, method='fdr_bh')
        for sig, fdr in zip(cl_asb_data, cl_fdr):
            sig['log10_fdr'] = np.nan if np.isnan(fdr) else -np.log10(fdr)

        logger.info('Ticket {}: cl tests done'.format(ticket_id))
        update_ticket_status(ticket, 'Finalizing the report')

        tf_asb_data = sorted(tf_asb_data, key=lambda x: (x['log10_fdr'], x['log10_p_value'], x['odds']), reverse=True)
        cl_asb_data = sorted(cl_asb_data, key=lambda x: (x['log10_fdr'], x['log10_p_value'], x['odds']), reverse=True)

        ticket.status = 'Processed'
        meta_info = dict(ticket.meta_info)
        meta_info.update({
            'processing_time': str(datetime.now() - processing_start_time),
            'all_rs': all_rs,
            'tf_asbs': tf_asbs,
            'tf_asbs_rs': tf_asbs_rs,
            'cl_asbs': cl_asbs,
            'cl_asbs_rs': cl_asbs_rs,
            'all_asbs': all_asbs,
            'all_asbs_rs': all_asbs_rs,
            'tf_candidates': tf_candidates,
            'tf_candidates_rs': tf_candidates_rs,
            'cl_candidates': cl_candidates,
            'cl_candidates_rs': cl_candidates_rs,
            'all_candidates': all_candidates,
            'all_candidates_rs': all_candidates_rs,
            'tf_odds': marshal_inf(tf_odds),
            'tf_log10_p_value': marshal_logp(tf_p),
            'cl_odds': marshal_inf(cl_odds),
            'cl_log10_p_value': marshal_logp(cl_p),
            'all_odds': marshal_inf(all_odds),
            'all_log10_p_value': marshal_logp(all_p),
            'tf_odds_rs': marshal_inf(tf_odds_rs),
            'tf_log10_p_value_rs': marshal_logp(tf_p_rs),
            'cl_odds_rs': marshal_inf(cl_odds_rs),
            'cl_log10_p_value_rs': marshal_logp(cl_p_rs),
            'all_odds_rs': marshal_inf(all_odds_rs),
            'all_log10_p_value_rs': -np.log10(all_p_rs),
            'expected_fraction_all': possible_all_asbs_rs / possible_all_candidates_rs,
            'expected_fraction_tf': possible_tf_asbs_rs / possible_tf_candidates_rs,
            'expected_fraction_cl': possible_cl_asbs_rs / possible_cl_candidates_rs,
            'tf_asb_counts': modify_counts(tf_asb_data, top=False),
            'tf_asb_counts_top': modify_counts(tf_asb_data, tf_sum_counts, top=True),
            'cl_asb_counts': modify_counts(cl_asb_data, top=False),
            'cl_asb_counts_top': modify_counts(cl_asb_data, cl_sum_counts, top=True),
            'tf_asb_data': marshall_data(tf_asb_data),
            'cl_asb_data': marshall_data(cl_asb_data),
            'concordant_asbs': conc_asbs,
        })

    except Exception as e:
        if not isinstance(e, ConvError):
            logger.error(e, exc_info=True)
        ticket.status = 'Failed'
        if change_status_on_fail:
            update_ticket_status(ticket, 'Processing failed while {}'.format(ticket.meta_info['status_details']))
        session.commit()
        return

    ticket.meta_info = meta_info
    logger.info('Ticket {}: ticket info changed'.format(ticket_id))
    session.commit()

    logger.info('Ticket {}: session commited'.format(ticket_id))
    update_ticket_status(ticket, 'Processing finished')
    session.commit()
