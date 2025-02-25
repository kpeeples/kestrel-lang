from pandas import DataFrame
from typeguard import typechecked
from uuid import UUID
from typing import (
    Mapping,
    MutableMapping,
    Optional,
    Iterable,
)

from kestrel.cache.base import AbstractCache
from kestrel.ir.graph import IRGraphEvaluable
from kestrel.ir.instructions import (
    Instruction,
    Return,
    Variable,
    SourceInstruction,
    TransformingInstruction,
)
from kestrel.interface.datasource.codegen.dataframe import (
    evaluate_source_instruction,
    evaluate_transforming_instruction,
)


@typechecked
class InMemoryCache(AbstractCache):
    def __init__(
        self,
        initial_cache: Mapping[UUID, DataFrame] = {},
        session_id: Optional[UUID] = None,
    ):
        super().__init__(session_id)
        self.cache: MutableMapping[UUID, DataFrame] = {}

        # update() will call __setitem__() internally
        self.update(initial_cache)

    def __del__(self):
        del self.cache

    def __getitem__(self, instruction_id: UUID) -> DataFrame:
        return self.cache[self.cache_catalog[instruction_id]]

    def __delitem__(self, instruction_id: UUID):
        del self.cache[instruction_id]
        del self.cache_catalog[instruction_id]

    def __setitem__(
        self,
        instruction_id: UUID,
        data: DataFrame,
    ):
        self.cache[instruction_id] = data
        self.cache_catalog[instruction_id] = instruction_id

    def evaluate_graph(
        self,
        graph: IRGraphEvaluable,
        instructions_to_evaluate: Optional[Iterable[Instruction]] = None,
    ) -> Mapping[UUID, DataFrame]:
        if not instructions_to_evaluate:
            instructions_to_evaluate = graph.get_sink_nodes()

        mapping = {}
        for ins in instructions_to_evaluate:
            df = self._evaluate_instruction_in_graph(graph, ins)
            self[ins.id] = df
            mapping[ins.id] = df

        return mapping

    def _evaluate_instruction_in_graph(
        self, graph: IRGraphEvaluable, instruction: Instruction
    ) -> DataFrame:
        # TODO: handle multiple predecessors of a node

        if isinstance(instruction, Return):
            df = self._evaluate_instruction_in_graph(
                graph, next(graph.predecessors(instruction))
            )
        elif isinstance(instruction, Variable):
            df = self._evaluate_instruction_in_graph(
                graph, next(graph.predecessors(instruction))
            )
            self[instruction.id] = df
        elif isinstance(instruction, SourceInstruction):
            df = evaluate_source_instruction(instruction)
        elif isinstance(instruction, TransformingInstruction):
            df0 = self._evaluate_instruction_in_graph(
                graph, next(graph.predecessors(instruction))
            )
            df = evaluate_transforming_instruction(instruction, df0)
        else:
            raise NotImplementedError(f"Unknown instruction type: {instruction}")
        return df
