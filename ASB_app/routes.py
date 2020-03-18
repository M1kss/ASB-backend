from ASB_app import api, logger, service
from ASB_app.serializers import rs_snp_model, rs_snp_model_full, transcription_factor_model, cell_line_model
from ASB_app.constants import chromosomes
from ASB_app.exceptions import ParsingError
from flask import request, jsonify, g
from flask_restplus import Resource, inputs
from sqlalchemy.orm.exc import NoResultFound
from flask_restplus import inputs

snp_nsp = api.namespace('SNPs', path='/snps', description='Access to Single Nucleotide Polymorphisms')
search_nsp = api.namespace('Search', path='/search', description='Search SNPs')


@snp_nsp.route('/<int:rs_id>/<string:alt>')
class SNPItem(Resource):
    @api.marshal_with(rs_snp_model_full)
    def get(self, rs_id, alt):
        """
        Get complete imformation about an SNP by rs-ID and alt allele
        """
        try:
            return service.get_full_snp(rs_id, alt)
        except NoResultFound:
            api.abort(404)


@search_nsp.route('/snps/rs/<int:rs_id>')
class SNPSearchSNPByIdCollection(Resource):
    @api.marshal_list_with(rs_snp_model)
    def get(self, rs_id):
        """
        Get all SNPs by rs-ID short info
        """
        return service.get_snps_by_rs_id(rs_id)


@search_nsp.route('/snps/gp/<string:chr>/<int:pos1>/<int:pos2>')
class SNPSearchSNPByGPCollection(Resource):
    @api.marshal_list_with(rs_snp_model)
    @api.response(507, 'Result too long')
    def get(self, chr, pos1, pos2):
        """
        Get all SNPs by genome position short info
        """
        result = service.get_snps_by_genome_position(chr, pos1, pos2)
        if len(result) > 1000:
            return [], 507
        return result


search_parser = api.parser()
search_parser.add_argument('cell_types', action='split')
search_parser.add_argument('transcription_factors', action='split')
search_parser.add_argument('chromosome', choices=chromosomes, help='Not a valid chromosome: {error_msg}')
search_parser.add_argument('start', type=inputs.positive)
search_parser.add_argument('end', type=inputs.positive)


@search_nsp.route('/snps/advanced')
class AdvancedSearchSNP(Resource):
    @api.marshal_list_with(rs_snp_model)
    @api.response(507, 'Result too long')
    @api.expect(search_parser)
    def get(self):
        """
        Get all SNPs with advanced filters short info
        """
        try:
            result = service.get_snps_by_advanced_filters(search_parser.parse_args())
            if len(result) > 1000:
                return [], 507
            return result
        except ParsingError:
            api.abort(400)


used_hints_parser = api.parser()
used_hints_parser.add_argument('options', action='split')
used_hints_parser.add_argument('search')


@search_nsp.route('/tf/hint')
class TransctiptionFactorHint(Resource):
    @api.expect(used_hints_parser)
    @api.marshal_list_with(transcription_factor_model)
    def get(self):
        args = used_hints_parser.parse_args()
        return service.get_hints('TF', args.get('search', ''), args.get('options', []))


@search_nsp.route('/cl/hint')
class CellLineHint(Resource):
    @api.expect(used_hints_parser)
    @api.marshal_list_with(cell_line_model)
    def get(self):
        args = used_hints_parser.parse_args()
        return service.get_hints('CL', args.get('search', ''), args.get('options', []))
