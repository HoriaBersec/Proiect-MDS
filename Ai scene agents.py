bl_info = {
    "name": "AI Scene Agents",
    "author": "Horia",
    "version": (1, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > AI Agents",
    "description": "Agenti AI (LLM + tool use) pentru management de scena si materiale procedurale",
    "category": "3D View",
}

import bpy
import json
import math
import urllib.request
import urllib.error

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5"
MAX_AGENT_TURNS = 15

# Tag pus pe nodurile create de agent, sters la re-rulare pe acelasi material.
NODE_TAG = "managed"


# --- Scene tools ---

def tool_get_scene_info():
    objects = []
    for obj in bpy.context.scene.objects:
        info = {
            "name": obj.name,
            "type": obj.type,
            "parent": obj.parent.name if obj.parent else None,
            "collections": [c.name for c in obj.users_collection],
            "location": [round(v, 3) for v in obj.location],
            "hidden": obj.hide_viewport,
        }
        if obj.type == 'LIGHT':
            info["light_type"] = obj.data.type
            info["energy"] = obj.data.energy
        if obj.type == 'MESH':
            info["materials"] = [m.name for m in obj.data.materials if m]
        objects.append(info)
    return json.dumps({
        "object_count": len(objects),
        "objects": objects,
        "collections": [c.name for c in bpy.data.collections],
    })


def tool_delete_objects(names):
    deleted, not_found = [], []
    for n in names:
        obj = bpy.data.objects.get(n)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
            deleted.append(n)
        else:
            not_found.append(n)
    return json.dumps({"deleted": deleted, "not_found": not_found})


def tool_rename_object(old_name, new_name):
    obj = bpy.data.objects.get(old_name)
    if not obj:
        return json.dumps({"error": f"Object '{old_name}' not found"})
    obj.name = new_name
    return json.dumps({"renamed": {old_name: obj.name}})


def tool_set_transform(name, location=None, rotation_deg=None, scale=None):
    obj = bpy.data.objects.get(name)
    if not obj:
        return json.dumps({"error": f"Object '{name}' not found"})
    changes = {}
    if location is not None:
        obj.location = tuple(location)
        changes["location"] = [round(v, 3) for v in obj.location]
    if rotation_deg is not None:
        obj.rotation_euler = tuple(math.radians(a) for a in rotation_deg)
        changes["rotation_deg"] = list(rotation_deg)
    if scale is not None:
        obj.scale = (scale, scale, scale) if isinstance(scale, (int, float)) else tuple(scale)
        changes["scale"] = [round(v, 3) for v in obj.scale]
    return json.dumps({"transformed": name, "changes": changes})


def tool_move_to_collection(collection_name, object_names):
    coll = bpy.data.collections.get(collection_name)
    created = False
    if not coll:
        coll = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(coll)
        created = True
    moved, not_found = [], []
    for name in object_names:
        obj = bpy.data.objects.get(name)
        if not obj:
            not_found.append(name)
            continue
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
        moved.append(name)
    return json.dumps({
        "collection": collection_name,
        "created_new": created,
        "moved": moved,
        "not_found": not_found,
    })


def tool_set_visibility(object_names, hidden=True):
    changed, not_found = [], []
    for name in object_names:
        obj = bpy.data.objects.get(name)
        if not obj:
            not_found.append(name)
            continue
        obj.hide_viewport = hidden
        obj.hide_render = hidden
        changed.append(name)
    return json.dumps({"hidden": hidden, "objects": changed, "not_found": not_found})


# --- Material helpers ---

def _get_or_create_material(material_name):
    mat = bpy.data.materials.get(material_name)
    if not mat:
        mat = bpy.data.materials.new(name=material_name)
    mat.use_nodes = True
    return mat


def _assign_material(obj, mat):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)


def _clear_managed_nodes(node_tree):
    for n in list(node_tree.nodes):
        if n.get(NODE_TAG):
            node_tree.nodes.remove(n)


def _rgba(color):
    c = list(color)
    while len(c) < 4:
        c.append(1.0)
    return c


# --- Material tools ---

def tool_create_procedural_material(object_name, material_name, base_color, spot_color,
                                     pattern="organic", scale=5.0, detail=2.0, contrast=0.5,
                                     vary_roughness=False, roughness_base=0.6,
                                     roughness_min=0.3, roughness_max=0.9,
                                     metallic=0.0):
    obj = bpy.data.objects.get(object_name)
    if not obj or obj.type != 'MESH':
        return json.dumps({"error": f"Mesh object '{object_name}' not found"})

    mat = _get_or_create_material(material_name)
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    if not bsdf:
        return json.dumps({"error": "Principled BSDF missing"})

    _clear_managed_nodes(nt)

    tex_coord = nodes.new('ShaderNodeTexCoord')
    tex_coord.location = (bsdf.location.x - 900, bsdf.location.y)
    tex_coord[NODE_TAG] = True

    if pattern == "cellular":
        pat = nodes.new('ShaderNodeTexVoronoi')
        pat.feature = 'F1'
        pat.inputs['Scale'].default_value = scale
        pat_output = pat.outputs['Distance']
        pattern_label = "Voronoi"
    else:
        pat = nodes.new('ShaderNodeTexNoise')
        pat.inputs['Scale'].default_value = scale
        pat.inputs['Detail'].default_value = detail
        pat.inputs['Roughness'].default_value = 0.5
        pat_output = pat.outputs['Fac']
        pattern_label = "Noise"
    pat.location = (bsdf.location.x - 700, bsdf.location.y)
    pat[NODE_TAG] = True
    links.new(tex_coord.outputs['Object'], pat.inputs['Vector'])

    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.location = (bsdf.location.x - 400, bsdf.location.y)
    ramp[NODE_TAG] = True
    links.new(pat_output, ramp.inputs['Fac'])

    els = ramp.color_ramp.elements
    while len(els) > 2:
        els.remove(els[-1])
    lo = max(0.0, min(1.0, contrast - 0.05))
    hi = max(0.0, min(1.0, contrast + 0.05))
    if hi <= lo:
        hi = min(1.0, lo + 0.01)
    els[0].position = lo
    els[0].color = _rgba(spot_color)
    els[1].position = hi
    els[1].color = _rgba(base_color)

    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Metallic'].default_value = metallic

    result = {"type": "procedural", "pattern": pattern_label}

    if vary_roughness:
        mr = nodes.new('ShaderNodeMapRange')
        mr.location = (bsdf.location.x - 400, bsdf.location.y - 300)
        mr[NODE_TAG] = True
        links.new(pat_output, mr.inputs['Value'])
        mr.inputs['From Min'].default_value = 0.0
        mr.inputs['From Max'].default_value = 1.0
        mr.inputs['To Min'].default_value = roughness_min
        mr.inputs['To Max'].default_value = roughness_max
        links.new(mr.outputs['Result'], bsdf.inputs['Roughness'])
        result["roughness"] = f"varied {roughness_min}-{roughness_max}"
    else:
        bsdf.inputs['Roughness'].default_value = roughness_base
        result["roughness"] = roughness_base

    _assign_material(obj, mat)
    return json.dumps(result)


def tool_create_weathered_material(object_name, material_name,
                                    base_color, base_metallic, base_roughness,
                                    wear_color, wear_metallic, wear_roughness,
                                    scale=8.0, detail=3.0,
                                    wear_amount=0.5, wear_hardness=0.5):
    obj = bpy.data.objects.get(object_name)
    if not obj or obj.type != 'MESH':
        return json.dumps({"error": f"Mesh object '{object_name}' not found"})

    mat = _get_or_create_material(material_name)
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = nodes.get("Principled BSDF")
    if not bsdf:
        return json.dumps({"error": "Principled BSDF missing"})

    _clear_managed_nodes(nt)

    tex_coord = nodes.new('ShaderNodeTexCoord')
    tex_coord.location = (bsdf.location.x - 1200, bsdf.location.y)
    tex_coord[NODE_TAG] = True

    noise = nodes.new('ShaderNodeTexNoise')
    noise.location = (bsdf.location.x - 1000, bsdf.location.y)
    noise[NODE_TAG] = True
    noise.inputs['Scale'].default_value = scale
    noise.inputs['Detail'].default_value = detail
    noise.inputs['Roughness'].default_value = 0.5
    links.new(tex_coord.outputs['Object'], noise.inputs['Vector'])

    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.location = (bsdf.location.x - 750, bsdf.location.y)
    ramp[NODE_TAG] = True
    links.new(noise.outputs['Fac'], ramp.inputs['Fac'])

    # wear_amount deplaseaza threshold-ul, wear_hardness ii controleaza latimea benzii de tranzitie
    center = 1.0 - wear_amount
    half_width = max(0.005, 0.4 * (1.0 - wear_hardness))
    lo = max(0.0, min(1.0, center - half_width))
    hi = max(0.0, min(1.0, center + half_width))
    if hi <= lo:
        hi = min(1.0, lo + 0.005)
    els = ramp.color_ramp.elements
    while len(els) > 2:
        els.remove(els[-1])
    els[0].position = lo
    els[0].color = (0.0, 0.0, 0.0, 1.0)
    els[1].position = hi
    els[1].color = (1.0, 1.0, 1.0, 1.0)
    mask = ramp.outputs['Alpha']

    mix_color = nodes.new('ShaderNodeMixRGB')
    mix_color.blend_type = 'MIX'
    mix_color.location = (bsdf.location.x - 400, bsdf.location.y + 200)
    mix_color[NODE_TAG] = True
    mix_color.inputs['Color1'].default_value = _rgba(base_color)
    mix_color.inputs['Color2'].default_value = _rgba(wear_color)
    links.new(mask, mix_color.inputs['Fac'])
    links.new(mix_color.outputs['Color'], bsdf.inputs['Base Color'])

    mr_met = nodes.new('ShaderNodeMapRange')
    mr_met.location = (bsdf.location.x - 400, bsdf.location.y - 50)
    mr_met[NODE_TAG] = True
    links.new(mask, mr_met.inputs['Value'])
    mr_met.inputs['From Min'].default_value = 0.0
    mr_met.inputs['From Max'].default_value = 1.0
    mr_met.inputs['To Min'].default_value = base_metallic
    mr_met.inputs['To Max'].default_value = wear_metallic
    links.new(mr_met.outputs['Result'], bsdf.inputs['Metallic'])

    mr_rough = nodes.new('ShaderNodeMapRange')
    mr_rough.location = (bsdf.location.x - 400, bsdf.location.y - 300)
    mr_rough[NODE_TAG] = True
    links.new(mask, mr_rough.inputs['Value'])
    mr_rough.inputs['From Min'].default_value = 0.0
    mr_rough.inputs['From Max'].default_value = 1.0
    mr_rough.inputs['To Min'].default_value = base_roughness
    mr_rough.inputs['To Max'].default_value = wear_roughness
    links.new(mr_rough.outputs['Result'], bsdf.inputs['Roughness'])

    _assign_material(obj, mat)
    return json.dumps({
        "type": "weathered",
        "wear_amount": wear_amount,
        "wear_hardness": wear_hardness,
    })


# --- Tool schemas ---

SCHEMA_GET_SCENE_INFO = {
    "name": "get_scene_info",
    "description": "Returneaza toate obiectele din scena si colectiile existente. Apeleaza-l intotdeauna primul.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

SCHEMA_DELETE_OBJECTS = {
    "name": "delete_objects",
    "description": "Sterge obiectele cu numele exacte date.",
    "input_schema": {
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["names"],
    },
}

SCHEMA_RENAME_OBJECT = {
    "name": "rename_object",
    "description": "Redenumeste un obiect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "old_name": {"type": "string"},
            "new_name": {"type": "string"},
        },
        "required": ["old_name", "new_name"],
    },
}

SCHEMA_SET_TRANSFORM = {
    "name": "set_transform",
    "description": "Seteaza pozitie/rotatie/scale pentru un obiect. Toate optionale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "location": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z] in metri"},
            "rotation_deg": {"type": "array", "items": {"type": "number"}, "description": "Euler [rx, ry, rz] in grade"},
            "scale": {"description": "Scalar sau vector [sx, sy, sz]"},
        },
        "required": ["name"],
    },
}

SCHEMA_MOVE_TO_COLLECTION = {
    "name": "move_to_collection",
    "description": "Muta obiectele intr-o colectie (o creeaza daca nu exista).",
    "input_schema": {
        "type": "object",
        "properties": {
            "collection_name": {"type": "string"},
            "object_names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["collection_name", "object_names"],
    },
}

SCHEMA_SET_VISIBILITY = {
    "name": "set_visibility",
    "description": "Ascunde (hidden=true) sau afiseaza (hidden=false) obiecte in viewport si render.",
    "input_schema": {
        "type": "object",
        "properties": {
            "object_names": {"type": "array", "items": {"type": "string"}},
            "hidden": {"type": "boolean"},
        },
        "required": ["object_names", "hidden"],
    },
}

SCHEMA_CREATE_PROCEDURAL = {
    "name": "create_procedural_material",
    "description": (
        "Un pattern (Noise sau Voronoi) -> ColorRamp -> Base Color, optional cu variatie de roughness. "
        "pattern='organic' foloseste Noise (pete moi); pattern='cellular' foloseste Voronoi (celule cu contur)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "material_name": {"type": "string"},
            "base_color": {"type": "array", "items": {"type": "number"}, "description": "[r,g,b] 0-1, culoarea dominanta"},
            "spot_color": {"type": "array", "items": {"type": "number"}, "description": "[r,g,b] 0-1, culoarea petelor"},
            "pattern": {"type": "string", "enum": ["organic", "cellular"]},
            "scale": {"type": "number", "description": "Mic (1-3) = pete mari; mare (15-30) = grain fin"},
            "detail": {"type": "number", "description": "0-16, detaliu fractal (doar organic)"},
            "contrast": {"type": "number", "description": "0-1, unde se face tranzitia intre culori"},
            "vary_roughness": {"type": "boolean"},
            "roughness_base": {"type": "number"},
            "roughness_min": {"type": "number"},
            "roughness_max": {"type": "number"},
            "metallic": {"type": "number"},
        },
        "required": ["object_name", "material_name", "base_color", "spot_color"],
    },
}

SCHEMA_CREATE_WEATHERED = {
    "name": "create_weathered_material",
    "description": (
        "Doua straturi PBR complete (culoare + metallic + roughness) amestecate printr-o masca de noise. "
        "Foloseste cand exista o baza peste care s-a depus/format un al doilea strat. "
        "wear_amount 0-1 = procent de acoperire cu wear. "
        "wear_hardness 0-1 = 0 tranzitie difuza, 1 margine dura."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "material_name": {"type": "string"},
            "base_color": {"type": "array", "items": {"type": "number"}},
            "base_metallic": {"type": "number"},
            "base_roughness": {"type": "number"},
            "wear_color": {"type": "array", "items": {"type": "number"}},
            "wear_metallic": {"type": "number"},
            "wear_roughness": {"type": "number"},
            "scale": {"type": "number"},
            "detail": {"type": "number"},
            "wear_amount": {"type": "number"},
            "wear_hardness": {"type": "number"},
        },
        "required": ["object_name", "material_name", "base_color", "base_metallic",
                     "base_roughness", "wear_color", "wear_metallic", "wear_roughness"],
    },
}

TOOL_EXECUTORS = {
    "get_scene_info": lambda inp: tool_get_scene_info(),
    "delete_objects": lambda inp: tool_delete_objects(inp.get("names", [])),
    "rename_object": lambda inp: tool_rename_object(inp["old_name"], inp["new_name"]),
    "set_transform": lambda inp: tool_set_transform(
        inp["name"], inp.get("location"), inp.get("rotation_deg"), inp.get("scale")),
    "move_to_collection": lambda inp: tool_move_to_collection(
        inp["collection_name"], inp.get("object_names", [])),
    "set_visibility": lambda inp: tool_set_visibility(
        inp.get("object_names", []), inp.get("hidden", True)),
    "create_procedural_material": lambda inp: tool_create_procedural_material(
        inp["object_name"], inp["material_name"], inp["base_color"], inp["spot_color"],
        inp.get("pattern", "organic"), inp.get("scale", 5.0), inp.get("detail", 2.0),
        inp.get("contrast", 0.5), inp.get("vary_roughness", False),
        inp.get("roughness_base", 0.6), inp.get("roughness_min", 0.3),
        inp.get("roughness_max", 0.9), inp.get("metallic", 0.0)),
    "create_weathered_material": lambda inp: tool_create_weathered_material(
        inp["object_name"], inp["material_name"],
        inp["base_color"], inp["base_metallic"], inp["base_roughness"],
        inp["wear_color"], inp["wear_metallic"], inp["wear_roughness"],
        inp.get("scale", 8.0), inp.get("detail", 3.0),
        inp.get("wear_amount", 0.5), inp.get("wear_hardness", 0.5)),
}


AGENTS = {
    "SCENE_MANAGER": {
        "label": "Scene Manager",
        "system": (
            "Esti un agent de management al scenei in Blender. Primesti comenzi in limbaj natural "
            "despre organizare, mutare, ascundere, redenumire si stergere.\n"
            "REGULI:\n"
            "1. Apeleaza intotdeauna get_scene_info primul.\n"
            "2. Potriveste semantic: cereri generice ('sterge pietrele') se aplica tuturor obiectelor "
            "care sunt acel tip de lucru, inclusiv duplicate cu sufix .001, .002. Nu te opri la match "
            "exact de text — un nume compus care descrie altceva (ex: un perete facut din piatra) NU "
            "e acelasi lucru cu obiectul in sine, chiar daca contine acelasi cuvant. Judeca dupa ce "
            "reprezinta obiectul in scena, nu doar dupa substring in nume.\n"
            "3. La stergerea luminilor 'ambientale' pastreaza SUN daca nu se cere altfel.\n"
            "4. Nu sterge camere fara cerere explicita.\n"
            "5. Pentru organizare foloseste move_to_collection cu nume descriptive.\n"
            "6. Pentru aliniere/distribuire calculeaza pozitiile si apeleaza set_transform.\n"
            "7. 'Ascunde X' = set_visibility, nu delete_objects.\n"
            "8. Raspunde scurt la final."
        ),
        "tools": [SCHEMA_GET_SCENE_INFO, SCHEMA_DELETE_OBJECTS, SCHEMA_RENAME_OBJECT,
                   SCHEMA_SET_TRANSFORM, SCHEMA_MOVE_TO_COLLECTION, SCHEMA_SET_VISIBILITY],
    },
    "MATERIAL_ARTIST": {
        "label": "Material Artist",
        "system": (
            "Esti un agent de materiale in Blender. Construiesti materiale Principled BSDF cu grafuri "
            "de noduri reale. Ai doua unelte:\n"
            "- create_procedural_material: un singur pattern (Noise organic sau Voronoi cellular) care "
            "variaza culoarea intre doua tonuri. Pentru suprafete cu variatie de un singur tip.\n"
            "- create_weathered_material: doua straturi PBR complete amestecate printr-o masca de noise. "
            "Pentru cazuri cu strat de baza + strat depus deasupra.\n"
            "REGULI:\n"
            "1. Apeleaza get_scene_info primul.\n"
            "2. Potriveste semantic numele obiectelor.\n"
            "3. Pentru weathered: base = starea curata, wear = stratul deteriorat (de obicei metallic 0, "
            "roughness mare). wear_hardness mare pentru margini dure, mic pentru tranzitii difuze.\n"
            "4. Pentru cereri de material uniform foloseste procedural cu spot_color aproape egal cu base_color.\n"
            "5. Raspunde scurt la final."
        ),
        "tools": [SCHEMA_GET_SCENE_INFO, SCHEMA_CREATE_PROCEDURAL, SCHEMA_CREATE_WEATHERED],
    },
}


# --- API + agent loop ---

def call_anthropic(api_key, system, tools, messages):
    payload = {
        "model": MODEL,
        "max_tokens": 2048,
        "system": system,
        "tools": tools,
        "messages": messages,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {e.code}: {detail}") from e


def run_agent(api_key, agent_key, user_prompt, log):
    agent = AGENTS[agent_key]
    messages = [{"role": "user", "content": user_prompt}]
    log(f"=== Agent: {agent['label']} ===")
    log(f"User: {user_prompt}\n")

    for _ in range(MAX_AGENT_TURNS):
        response = call_anthropic(api_key, agent["system"], agent["tools"], messages)
        content = response.get("content", [])
        tool_results = []

        for block in content:
            if block["type"] == "text" and block["text"].strip():
                log(f"[{agent['label']}] {block['text'].strip()}")
            elif block["type"] == "tool_use":
                name, tool_input = block["name"], block["input"]
                log(f"  -> tool: {name}({json.dumps(tool_input, ensure_ascii=False)})")
                try:
                    result = TOOL_EXECUTORS[name](tool_input)
                except Exception as ex:
                    result = json.dumps({"error": str(ex)})
                log(f"  <- {result[:400]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

        if response.get("stop_reason") == "tool_use" and tool_results:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": tool_results})
        else:
            log("\n=== Gata. ===")
            return

    log("\n=== Oprit: limita de tururi atinsa. ===")


# --- Blender UI ---

class AIAgentsPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key: bpy.props.StringProperty(
        name="Anthropic API Key",
        description="Cheia API (console.anthropic.com)",
        subtype='PASSWORD',
        default="",
    )

    def draw(self, context):
        self.layout.prop(self, "api_key")


class AIAGENTS_OT_run(bpy.types.Operator):
    bl_idname = "ai_agents.run"
    bl_label = "Run Agent"
    bl_description = "Trimite promptul agentului selectat"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        scene = context.scene

        if not prefs.api_key.strip():
            self.report({'ERROR'}, "Seteaza API key in Edit > Preferences > Add-ons > AI Scene Agents")
            return {'CANCELLED'}
        if not scene.ai_agents_prompt.strip():
            self.report({'ERROR'}, "Scrie un prompt")
            return {'CANCELLED'}

        text = bpy.data.texts.get("AgentLog") or bpy.data.texts.new("AgentLog")

        def log(line):
            text.write(line + "\n")
            print("[AgentLog]", line)

        try:
            run_agent(prefs.api_key.strip(), scene.ai_agents_agent,
                      scene.ai_agents_prompt.strip(), log)
        except Exception as ex:
            log(f"EROARE: {ex}")
            self.report({'ERROR'}, f"Agent error: {ex}")
            return {'CANCELLED'}

        self.report({'INFO'}, "Agent terminat. Vezi AgentLog in Text Editor.")
        return {'FINISHED'}


class AIAGENTS_PT_panel(bpy.types.Panel):
    bl_label = "AI Agents"
    bl_idname = "AIAGENTS_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI Agents"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "ai_agents_agent", text="Agent")
        layout.prop(scene, "ai_agents_prompt", text="")
        layout.operator("ai_agents.run", icon='PLAY')
        layout.label(text="Log: Text Editor > AgentLog")


CLASSES = (AIAgentsPreferences, AIAGENTS_OT_run, AIAGENTS_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ai_agents_agent = bpy.props.EnumProperty(
        name="Agent",
        items=[(k, v["label"], v["system"][:60]) for k, v in AGENTS.items()],
        default="SCENE_MANAGER",
    )
    bpy.types.Scene.ai_agents_prompt = bpy.props.StringProperty(
        name="Prompt",
        description="Comanda in limbaj natural pentru agent",
        default="",
    )


def unregister():
    for cls in CLASSES:
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.ai_agents_agent
    del bpy.types.Scene.ai_agents_prompt


if __name__ == "__main__":
    register()
