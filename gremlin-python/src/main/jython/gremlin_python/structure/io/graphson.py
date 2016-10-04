'''
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
'''
import json
from aenum import Enum

import six

from gremlin_python import statics
from gremlin_python.statics import FloatType, FunctionType, IntType, LongType, TypeType
from gremlin_python.process.traversal import Binding
from gremlin_python.process.traversal import Bytecode
from gremlin_python.process.traversal import P
from gremlin_python.process.traversal import Traversal
from gremlin_python.process.traversal import Traverser
from gremlin_python.process.traversal import TraversalStrategy
from gremlin_python.structure.graph import Edge
from gremlin_python.structure.graph import Property
from gremlin_python.structure.graph import Vertex
from gremlin_python.structure.graph import VertexProperty
from gremlin_python.structure.graph import Path


_serializers = {}
_deserializers = {}


class GraphSONTypeType(type):
    def __new__(mcs, name, bases, dct):
        cls = super(GraphSONTypeType, mcs).__new__(mcs, name, bases, dct)
        if not name.startswith('_'):
            if cls.python_type:
                _serializers[cls.python_type] = cls
            if cls.graphson_type:
                _deserializers[cls.graphson_type] = cls
        return cls


class GraphSONIO(object):

    _TYPE_KEY = "@type"
    _VALUE_KEY = "@value"

    def __init__(self, serializer_map=None, deserializer_map=None):
        """
        :param serializer_map: map from Python type to serializer instance implementing `dictify`
        :param deserializer_map: map from GraphSON type tag to deserializer instance implementing `objectify`
        """
        self.serializers = _serializers.copy()
        if serializer_map:
            self.serializers.update(serializer_map)

        self.deserializers = _deserializers.copy()
        if deserializer_map:
            self.deserializers.update(deserializer_map)

    def writeObject(self, objectData):
        # to JSON
        return json.dumps(self.toDict(objectData), separators=(',', ':'))

    def readObject(self, jsonData):
        # from JSON
        return self.toObject(json.loads(jsonData))

    def toDict(self, obj):
        """
        Encodes python objects in GraphSON type-tagged dict values
        """
        for key, serializer in self.serializers.items():
            if isinstance(obj, key):
                return serializer.dictify(obj, self)
        # list and map are treated as normal json objs (could be isolated serializers)
        if isinstance(obj, (list, set)):
            return [self.toDict(o) for o in obj]
        elif isinstance(obj, dict):
            return dict((self.toDict(k), self.toDict(v)) for k, v in obj.items())
        else:
            return obj

    def toObject(self, obj):
        """
        Unpacks GraphSON type-tagged dict values into objects mapped in self.deserializers
        """
        if isinstance(obj, dict):
            try:
                return self.deserializers[obj[self._TYPE_KEY]].objectify(obj[self._VALUE_KEY], self)
            except KeyError:
                pass
            # list and map are treated as normal json objs (could be isolated deserializers)
            return dict((self.toObject(k), self.toObject(v)) for k, v in obj.items())
        elif isinstance(obj, list):
            return [self.toObject(o) for o in obj]
        else:
            return obj

    def typedValue(self, type_name, value, prefix="g"):
        out = {self._TYPE_KEY: prefix + ":" + type_name}
        if value is not None:
            out[self._VALUE_KEY] = value
        return out


@six.add_metaclass(GraphSONTypeType)
class _GraphSONTypeIO(object):
    python_type = None
    graphson_type = None

    symbolMap = {"global_": "global", "as_": "as", "in_": "in", "and_": "and",
                 "or_": "or", "is_": "is", "not_": "not", "from_": "from",
                 "set_": "set", "list_": "list", "all_": "all"}

    @classmethod
    def unmangleKeyword(cls, symbol):
        return cls.symbolMap.get(symbol, symbol)

    def dictify(self, obj, graphson_io):
        raise NotImplementedError()

    def objectify(self, d, graphson_io):
        raise NotImplementedError()


class _BytecodeSerializer(_GraphSONTypeIO):

    @classmethod
    def _dictify_instructions(cls, instructions, graphson_io):
        out = []
        for instruction in instructions:
            inst = [instruction[0]]
            inst.extend(graphson_io.toDict(arg) for arg in instruction[1:])
            out.append(inst)
        return out

    @classmethod
    def dictify(cls, bytecode, graphson_io):
        if isinstance(bytecode, Traversal):
            bytecode = bytecode.bytecode
        out = {}
        if bytecode.source_instructions:
            out["source"] = cls._dictify_instructions(bytecode.source_instructions, graphson_io)
        if bytecode.step_instructions:
            out["step"] = cls._dictify_instructions(bytecode.step_instructions, graphson_io)
        return graphson_io.typedValue("Bytecode", out)


class TraversalSerializer(_BytecodeSerializer):
    python_type = Traversal


class BytecodeSerializer(_BytecodeSerializer):
    python_type = Bytecode


class TraversalStrategySerializer(_GraphSONTypeIO):
    python_type = TraversalStrategy

    @classmethod
    def dictify(cls, strategy, graphson_io):
        return graphson_io.typedValue(strategy.strategy_name, graphson_io.toDict(strategy.configuration))


class TraverserIO(_GraphSONTypeIO):
    python_type = Traverser
    graphson_type = "g:Traverser"

    @classmethod
    def dictify(cls, traverser, graphson_io):
        return graphson_io.typedValue("Traverser", {"value": graphson_io.toDict(traverser.object),
                                                     "bulk": graphson_io.toDict(traverser.bulk)})

    @classmethod
    def objectify(cls, d, graphson_io):
        return Traverser(graphson_io.toObject(d["value"]),
                         graphson_io.toObject(d["bulk"]))


class EnumSerializer(_GraphSONTypeIO):
    python_type = Enum

    @classmethod
    def dictify(cls, enum, graphson_io):
        return graphson_io.typedValue(cls.unmangleKeyword(type(enum).__name__),
                                      cls.unmangleKeyword(str(enum.name)))


class PSerializer(_GraphSONTypeIO):
    python_type = P

    @classmethod
    def dictify(cls, p, graphson_io):
        out = {"predicate": p.operator,
               "value": [graphson_io.toDict(p.value), graphson_io.toDict(p.other)] if p.other is not None else
                        graphson_io.toDict(p.value)}
        return graphson_io.typedValue("P", out)


class BindingSerializer(_GraphSONTypeIO):
    python_type = Binding

    @classmethod
    def dictify(cls, binding, graphson_io):
        out = {"key": binding.key,
               "value": graphson_io.toDict(binding.value)}
        return graphson_io.typedValue("Binding", out)


class LambdaSerializer(_GraphSONTypeIO):
    python_type = FunctionType

    @classmethod
    def dictify(cls, lambda_object, graphson_io):
        lambda_result = lambda_object()
        script = lambda_result if isinstance(lambda_result, str) else lambda_result[0]
        language = statics.default_lambda_language if isinstance(lambda_result, str) else lambda_result[1]
        out = {"script": script,
               "language": language}
        if language == "gremlin-jython" or language == "gremlin-python":
            if not script.strip().startswith("lambda"):
                script = "lambda " + script
                out["script"] = script
            out["arguments"] = six.get_function_code(eval(out["script"])).co_argcount
        else:
            out["arguments"] = -1
        return graphson_io.typedValue("Lambda", out)


class TypeSerializer(_GraphSONTypeIO):
    python_type = TypeType

    @classmethod
    def dictify(cls, typ, graphson_io):
        return graphson_io.toDict(typ())


class _NumberIO(_GraphSONTypeIO):

    @classmethod
    def dictify(cls, n, graphson_io):
        if isinstance(n, bool):  # because isinstance(False, int) and isinstance(True, int)
            return n
        return graphson_io.typedValue(cls.graphson_base_type, n)

    @classmethod
    def objectify(cls, v, _):
        return cls.python_type(v)


class FloatIO(_NumberIO):
    python_type = FloatType
    graphson_type = "g:Float"
    graphson_base_type = "Float"


class DoubleIO(FloatIO):
    graphson_type = "g:Double"
    graphson_base_type = "Double"


class Int64IO(_NumberIO):
    python_type = LongType
    graphson_type = "g:Int64"
    graphson_base_type = "Int64"


class Int32IO(_NumberIO):
    python_type = IntType
    graphson_type = "g:Int32"
    graphson_base_type = "Int32"


class VertexDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Vertex"

    @classmethod
    def objectify(cls, d, graphson_io):
        return Vertex(graphson_io.toObject(d["id"]), d.get("label", ""))


class EdgeDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Edge"

    @classmethod
    def objectify(cls, d, graphson_io):
        return Edge(graphson_io.toObject(d["id"]),
                    Vertex(graphson_io.toObject(d["outV"]), ""),
                    d.get("label", "vertex"),
                    Vertex(graphson_io.toObject(d["inV"]), ""))


class VertexPropertyDeserializer(_GraphSONTypeIO):
    graphson_type = "g:VertexProperty"

    @classmethod
    def objectify(cls, d, graphson_io):
        return VertexProperty(graphson_io.toObject(d["id"]), d["label"],
                              graphson_io.toObject(d["value"]))


class PropertyDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Property"

    @classmethod
    def objectify(cls, d, graphson_io):
        return Property(d["key"], graphson_io.toObject(d["value"]))


class PathDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Path"

    @classmethod
    def objectify(cls, d, graphson_io):
        labels = [set(label) for label in d["labels"]]
        objects = [graphson_io.toObject(o) for o in d["objects"]]
        return Path(labels, objects)
