"""The six `eval_dg_*` tables from the PoC Build Brief's minimal data model (subset of FSD 5.1).

Dropped for the PoC (real in production): committee members, consistency flags, audit log,
exports, comparison snapshot. Comparison and ranking are computed live from the verdict table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tender(Base):
    __tablename__ = "eval_dg_tenders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str | None] = mapped_column(String(500))
    tender_ref_no: Mapped[str | None] = mapped_column(String(120))
    authority_name: Mapped[str | None] = mapped_column(String(255))
    estimated_value: Mapped[float | None] = mapped_column(Float)
    issue_date: Mapped[str | None] = mapped_column(String(20))
    tender_doc_name: Mapped[str] = mapped_column(String(255))
    tender_doc_path: Mapped[str] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    award_mechanism: Mapped[str | None] = mapped_column(String(40))  # L1 | QCBS | unknown
    award_mechanism_detail: Mapped[dict | None] = mapped_column(JSON)  # label, weights, page, quote
    # Status is a label only in the PoC (Brief: "show status as a label; don't enforce the state machine").
    status: Mapped[str] = mapped_column(String(40), default="Draft")
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|extracting|ready|failed
    extraction_error: Mapped[str | None] = mapped_column(Text)
    # Hybrid pipeline state: local results land first, OCR refines them in the background.
    facts: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    ocr_status: Mapped[str] = mapped_column(String(20), default="not_needed")  # not_needed|queued|running|done|failed|unavailable
    ocr_progress: Mapped[dict] = mapped_column(JSON, default=dict)  # {done, total, cached, errors}
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    documents: Mapped[list[TenderDocument]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="TenderDocument.position"
    )

    criteria: Mapped[list[Criterion]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Criterion.display_order"
    )
    bidders: Mapped[list[Bidder]] = relationship(
        back_populates="tender", cascade="all, delete-orphan", order_by="Bidder.created_at"
    )


class TenderDocument(Base):
    """One file of the tender bundle (after expanding zip/rar)."""

    __tablename__ = "eval_dg_tender_documents"
    __table_args__ = (Index("idx_tender_docs_tender_id", "tender_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_tenders.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    file_name: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(500))  # "work_1234567.zip › Tenderdocuments.rar › DATASHEET.pdf"
    file_path: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str] = mapped_column(String(30))
    page_count: Mapped[int | None] = mapped_column(Integer)
    page_kinds: Mapped[list] = mapped_column(JSON, default=list)  # per page: english|marathi|garbled|image|ocr_en|ocr_mr
    ocr_modes: Mapped[dict] = mapped_column(JSON, default=dict)  # page -> fast|balanced
    error: Mapped[str | None] = mapped_column(Text)

    tender: Mapped[Tender] = relationship(back_populates="documents")


class Criterion(Base):
    __tablename__ = "eval_dg_criteria_registry"
    __table_args__ = (Index("idx_criteria_tender_id", "tender_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_tenders.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(255))
    bucket: Mapped[str] = mapped_column(String(40))  # Financial | Technical | Eligibility | Commercial
    tier: Mapped[str] = mapped_column(String(40))  # deterministic | llm_judge
    datatype: Mapped[str] = mapped_column(String(40))  # currency | number | boolean | composite
    operator: Mapped[str] = mapped_column(String(40))  # >= | <= | exists | exists_all | similar_project
    threshold_value: Mapped[float | None] = mapped_column(Float)
    threshold_unit: Mapped[str | None] = mapped_column(String(20))
    threshold_display: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # extraction hints (terms, windows, parts)
    gating: Mapped[bool] = mapped_column(Boolean, default=True)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_quote: Mapped[str | None] = mapped_column(Text)
    source_clause: Mapped[str | None] = mapped_column(String(40))
    source_section: Mapped[str | None] = mapped_column(String(255))
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("eval_dg_tender_documents.id", ondelete="SET NULL"))
    extraction_method: Mapped[str] = mapped_column(String(20), default="deterministic")  # deterministic|llm|pmc_standard|checklist
    status: Mapped[str] = mapped_column(String(20), default="located")  # located | located_marathi | derived
    derivation: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    system_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    tender: Mapped[Tender] = relationship(back_populates="criteria")


class Bidder(Base):
    __tablename__ = "eval_dg_bidders"
    __table_args__ = (Index("idx_bidders_tender_id", "tender_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_tenders.id", ondelete="CASCADE"))
    bidder_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="Registered")  # Registered|Extracting|ClaimsReady|Failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # A bid is a bundle too (MahaTender: Fee_PreQual_Technical_<bid id>.zip = EMD receipt + one merged
    # scan of every document), read locally first and refined by OCR like a tender.
    bid_ref: Mapped[str | None] = mapped_column(String(40))  # the portal's bid id, when the upload names it
    facts: Mapped[dict | None] = mapped_column(JSON)  # identity read from the documents: firm, constitution, ...
    warnings: Mapped[list | None] = mapped_column(JSON)
    ocr_status: Mapped[str | None] = mapped_column(String(20))  # not_needed|queued|running|done|failed|unavailable
    ocr_progress: Mapped[dict | None] = mapped_column(JSON)
    timings: Mapped[dict | None] = mapped_column(JSON)

    tender: Mapped[Tender] = relationship(back_populates="bidders")
    documents: Mapped[list[BidderDocument]] = relationship(back_populates="bidder", cascade="all, delete-orphan",
                                                           order_by="BidderDocument.position")
    extracted_values: Mapped[list[BidderExtractedValue]] = relationship(
        back_populates="bidder", cascade="all, delete-orphan"
    )
    verdicts: Mapped[list[BidderCriterionVerdict]] = relationship(
        back_populates="bidder", cascade="all, delete-orphan"
    )


class BidderDocument(Base):
    __tablename__ = "eval_dg_bidder_documents"
    __table_args__ = (Index("idx_docs_bidder_id", "bidder_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bidder_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_bidders.id", ondelete="CASCADE"))
    doc_type: Mapped[str] = mapped_column(String(30), default="combined")  # combined | emd_receipt | ...
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    position: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str | None] = mapped_column(String(500))  # "Fee_PreQual_Technical_1234567.zip › emd.pdf"
    file_hash: Mapped[str | None] = mapped_column(String(64))
    page_kinds: Mapped[list | None] = mapped_column(JSON)  # readability per page, as for tender documents
    page_types: Mapped[list | None] = mapped_column(JSON)  # what each page is: turnover_certificate, pan, ...
    ocr_modes: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    bidder: Mapped[Bidder] = relationship(back_populates="documents")


class BidderExtractedValue(Base):
    __tablename__ = "eval_dg_bidder_extracted_values"
    __table_args__ = (Index("idx_extracted_bidder_criteria", "bidder_id", "criteria_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    bidder_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_bidders.id", ondelete="CASCADE"))
    criteria_key: Mapped[str] = mapped_column(String(80))  # registry key, or "quoted_price"
    document_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_bidder_documents.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(String(255))
    value_numeric: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_page: Mapped[int] = mapped_column(Integer)
    evidence_quote: Mapped[str] = mapped_column(Text)
    evidence_section: Mapped[str | None] = mapped_column(String(255))
    extraction_tier: Mapped[str] = mapped_column(String(20), default="deterministic")  # deterministic | llm
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    bidder: Mapped[Bidder] = relationship(back_populates="extracted_values")
    document: Mapped[BidderDocument] = relationship()


class BidderCriterionVerdict(Base):
    __tablename__ = "eval_dg_bidder_criterion_verdicts"
    __table_args__ = (Index("idx_verdicts_tender_bidder", "tender_id", "bidder_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tender_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_tenders.id", ondelete="CASCADE"))
    bidder_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_bidders.id", ondelete="CASCADE"))
    criterion_id: Mapped[str] = mapped_column(ForeignKey("eval_dg_criteria_registry.id", ondelete="CASCADE"))
    criteria_key: Mapped[str] = mapped_column(String(80))
    verdict: Mapped[str] = mapped_column(String(10))  # pass | fail | unknown
    verdict_tier: Mapped[str] = mapped_column(String(20))  # deterministic | ai_assisted | rule_based
    unknown_kind: Mapped[str | None] = mapped_column(String(30))  # not_found | conflict | low_confidence | unparseable
    reason: Mapped[str] = mapped_column(Text)
    computation: Mapped[str | None] = mapped_column(Text)
    # Dual citation: requirement source (tender) ...
    requirement_tender_page: Mapped[int | None] = mapped_column(Integer)
    requirement_quote: Mapped[str | None] = mapped_column(Text)
    requirement_document_id: Mapped[str | None] = mapped_column(String(36))
    # ... and evidence source (bidder). Primary evidence in columns; all evidence (e.g. both
    # sides of a contradiction) in evidence_json so nothing is silently dropped.
    evidence_document_id: Mapped[str | None] = mapped_column(ForeignKey("eval_dg_bidder_documents.id"))
    evidence_page: Mapped[int | None] = mapped_column(Integer)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    system_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    bidder: Mapped[Bidder] = relationship(back_populates="verdicts")
    criterion: Mapped[Criterion] = relationship()


class Meta(Base):
    __tablename__ = "eval_dg_meta"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
