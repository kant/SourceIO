import random
import re
from pathlib import Path
import numpy as np
from .bsp_file import BSPFile
from .lump import LumpTypes, ModelLump, VertexLump, EdgeLump, FaceLump, SurfEdgeLump, StringsLump, StringOffsetLump, \
    TextureDataLump, TextureInfoLump, WorldLightLump
from .datatypes.world_light import EmitType, Color32

import bpy

material_name_fix = re.compile(r"_[\d]+_[\d]+_[\d]+")


def get_material(mat_name, model_ob):
    if mat_name:
        if material_name_fix.search(mat_name):
            mat_name = material_name_fix.sub("", mat_name)
        mat_name = mat_name
    else:
        mat_name = "Material"
    mat_ind = 0
    md = model_ob.data
    mat = None
    for candidate in bpy.data.materials:  # Do we have this material already?
        if candidate.name == mat_name:
            mat = candidate
    if mat:
        if md.materials.get(mat.name):  # Look for it on this mesh_data
            for i in range(len(md.materials)):
                if md.materials[i].name == mat.name:
                    mat_ind = i
                    break
        else:  # material exists, but not on this mesh_data
            md.materials.append(mat)
            mat_ind = len(md.materials) - 1
    else:  # material does not exist
        mat = bpy.data.materials.new(mat_name)
        md.materials.append(mat)
        # Give it a random colour
        rand_col = []
        for i in range(3):
            rand_col.append(random.uniform(.4, 1))
        rand_col.append(1.0)
        mat.diffuse_color = rand_col

        mat_ind = len(md.materials) - 1

    return mat_ind


class BSP:
    scale = 0.01

    def __init__(self, map_path):
        self.filepath = Path(map_path)
        self.map_file = BSPFile(self.filepath)
        self.map_file.parse()
        self.main_collection = bpy.data.collections.new(self.filepath.name)
        bpy.context.scene.collection.children.link(self.main_collection)

    def load_map_mesh(self):

        model_lump: ModelLump = self.map_file.lumps.get(LumpTypes.LUMP_MODELS, False)
        vertex_lump: VertexLump = self.map_file.lumps.get(LumpTypes.LUMP_VERTICES, False)
        edge_lump: EdgeLump = self.map_file.lumps.get(LumpTypes.LUMP_EDGES, False)
        suef_edge_lump: SurfEdgeLump = self.map_file.lumps.get(LumpTypes.LUMP_SURFEDGES, False)
        face_lump: FaceLump = self.map_file.lumps.get(LumpTypes.LUMP_FACES, False)
        texture_info_lump: TextureInfoLump = self.map_file.lumps.get(LumpTypes.LUMP_TEXINFO, False)
        texture_data_lump: TextureDataLump = self.map_file.lumps.get(LumpTypes.LUMP_TEXDATA, False)
        strings_lump: StringsLump = self.map_file.lumps.get(LumpTypes.LUMP_TEXDATA_STRING_TABLE, False)

        def get_string(string_id):
            return strings_lump.strings[string_id] or "NO_NAME"

        if vertex_lump and face_lump and model_lump:
            vertex_lump.vertices = np.multiply(vertex_lump.vertices, self.scale)
            for n, model in enumerate(model_lump.models):
                print(f"Loading model {n}/{len(model_lump.models)}")
                mesh_obj = bpy.data.objects.new(f"{self.filepath.stem}_{n}",
                                                bpy.data.meshes.new(f"{self.filepath.stem}_{n}_MESH"))
                self.main_collection.objects.link(mesh_obj)
                mesh_obj.location = model.origin

                mesh_data = mesh_obj.data
                material_lookup_table = {}
                for texture_info in texture_info_lump.texture_info:
                    texture_data = texture_data_lump.texture_data[texture_info.texture_data_id]
                    material_name = get_string(texture_data.name_id)
                    material_lookup_table[texture_data.name_id] = get_material(material_name, mesh_obj)

                faces = []
                material_indices = []
                for map_face in face_lump.faces[model.first_face:model.first_face + model.face_count]:
                    face = []
                    first_edge = map_face.first_edge
                    edge_count = map_face.edge_count

                    texture_info = texture_info_lump.texture_info[map_face.tex_info_id]
                    texture_data = texture_data_lump.texture_data[texture_info.texture_data_id]
                    for surf_edge in suef_edge_lump.surf_edges[first_edge:first_edge + edge_count]:
                        edge = edge_lump.edges[abs(surf_edge)]
                        if surf_edge < 0:
                            face.append(edge[1])
                        else:
                            face.append(edge[0])
                    material_indices.append(material_lookup_table[texture_data.name_id])

                    faces.append(face[::-1])
                mesh_data.from_pydata(vertex_lump.vertices, [], faces)

                for m, mat_id in enumerate(material_indices):
                    mesh_data.polygons[m].material_index = mat_id

                # mesh_data.uv_layers.new()
                # uv_data = mesh_data.uv_layers[0].data
                # for n, map_face in enumerate(face_lump.faces[model.first_face:model.first_face + model.face_count]):
                #     if map_face.tex_info_id == -1:
                #         continue
                #
                #     mesh_data.polygons[n].material_index = get_material(material_name, mesh_obj)

                #     first_edge = map_face.first_edge
                #     edge_count = map_face.edge_count

                # for surf_edge in suef_edge_lump.surf_edges[first_edge:first_edge + edge_count]:
                #     edges = edge_lump.edges[abs(surf_edge)]
                #     vertex_id = edges[1] if surf_edge < 0 else edges[0]
                #     co = np.array(vertex_lump.vertices[vertex_id])
                #     tv1, tv2 = texture_info.texture_vectors
                #     uco = np.array(tv1[:-1])
                #     vco = np.array(tv2[:-1])
                #     u = np.dot(co, uco) + tv1[3]
                #     v = np.dot(co, vco) + tv2[3]
                #     uv_data[vertex_id].uv = [u / texture_data.width, v / texture_data.height]

    def load_lights(self):
        lights_lump: WorldLightLump = self.map_file.lumps.get(LumpTypes.LUMP_WORLDLIGHTS, False)

        for light in lights_lump.lights:
            loc = np.multiply(light.origin, self.scale)
            if light.type == EmitType.emit_point:
                bpy.ops.object.light_add(type='POINT', align='WORLD', location=loc)
            elif light.type == EmitType.emit_spotlight:
                bpy.ops.object.light_add(type='SPOT', align='WORLD', location=loc)
            elif light.type == EmitType.emit_skylight:
                bpy.ops.object.light_add(type='SUN', align='WORLD', location=loc)
            elif light.type == EmitType.emit_skyambient:
                bpy.ops.object.light_add(type='AREA', radius=1, align='WORLD', location=loc)
            else:
                print("unsupported light type", light.type.name)
                continue
            lamp = bpy.context.object
            lamp_data = lamp.data
            lamp_data.energy = light.intensity.magnitude() * self.scale
            lamp_data.color = light.intensity.normalized().rgb
            lamp.rotation_euler = (360 * light.normal[0], 360 * light.normal[1], 360 * light.normal[2])

            print(light)