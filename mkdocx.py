#!/usr/bin/env python
# -*- coding: utf-8 -*-
# REPORT.txt 转 docx

import os
import re
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, 'REPORT.txt')
DST = os.path.join(BASE, '给梨加速器任务中心问题报告.docx')

doc = Document()

for s in doc.sections:
    s.top_margin = Cm(2.2)
    s.bottom_margin = Cm(2.2)
    s.left_margin = Cm(2.5)
    s.right_margin = Cm(2.5)

st = doc.styles['Normal']
st.font.name = '宋体'
st.font.size = Pt(10.5)
st.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
st.paragraph_format.line_spacing = 1.5
st.paragraph_format.space_after = Pt(3)


def set_cjk(run, font):
    """CJK 字体要单独设，rPr 可能为空"""
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rf)
    rf.set(qn('w:eastAsia'), font)
    rf.set(qn('w:ascii'), font)
    rf.set(qn('w:hAnsi'), font)


def add_code(text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.8)
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    set_cjk(r, 'Consolas')
    r.font.size = Pt(9)
    return p


lines = open(SRC, encoding='utf-8').read().split('\n')
i = 0
code_buf = []

while i < len(lines):
    line = lines[i].rstrip()
    is_code = bool(re.match(r'^ {2,}\S', line))

    if is_code:
        code_buf.append(line)
        i += 1
        continue
    if code_buf:
        add_code('\n'.join(code_buf))
        code_buf = []

    if not line.strip():
        i += 1
        continue

    # 首部标题
    if i < 2:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(10)
        r = p.add_run(line.strip())
        r.bold = True
        r.font.size = Pt(16)
        set_cjk(r, '黑体')
        i += 1
        continue

    # 元信息
    if i < 8 and '：' in line:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(line.strip())
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
        set_cjk(r, '宋体')
        i += 1
        continue

    # 一级标题
    if re.match(r'^[一二三四五六七八九十]+、', line):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(line.strip())
        r.bold = True
        r.font.size = Pt(13)
        set_cjk(r, '黑体')
        i += 1
        continue

    # 二级标题
    if re.match(r'^\d+\.\d+\s', line):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(9)
        p.paragraph_format.space_after = Pt(4)
        r = p.add_run(line.strip())
        r.bold = True
        r.font.size = Pt(11)
        set_cjk(r, '黑体')
        i += 1
        continue

    if set(line.strip()) <= {'-'} and len(line.strip()) > 3:
        i += 1
        continue

    p = doc.add_paragraph()
    r = p.add_run(line.strip())
    set_cjk(r, '宋体')
    i += 1

if code_buf:
    add_code('\n'.join(code_buf))

# 表格：把那几处对齐的数据改成真表格
TBL_AFTER = '同一账号，连续 11 个带 advertLink 字段的子任务'
TBL_ROWS = [
    ('项目', '观测前', '观测后', '变化'),
    ('score（积分）', '319600', '320480', '+880'),
    ('remain_acc_second（秒）', '239700', '240360', '+660'),
]
setup_done = False

doc.save(DST)
print('输出:', DST)
print('大小:', os.path.getsize(DST), 'bytes')
