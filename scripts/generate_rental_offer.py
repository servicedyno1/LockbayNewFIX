#!/usr/bin/env python3
"""
Generate a professional PDF rental offer letter
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import os

def create_rental_offer_pdf():
    """Generate professional rental offer letter PDF"""
    
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    filename = os.path.join(output_dir, "Rental_Offer_Letter.pdf")
    
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='Justify',
        alignment=TA_JUSTIFY,
        fontSize=11,
        leading=16,
        fontName='Times-Roman',
        spaceBefore=6,
        spaceAfter=6
    ))
    
    styles.add(ParagraphStyle(
        name='SenderAddress',
        alignment=TA_LEFT,
        fontSize=11,
        leading=14,
        fontName='Times-Roman'
    ))
    
    styles.add(ParagraphStyle(
        name='Subject',
        alignment=TA_LEFT,
        fontSize=11,
        leading=14,
        fontName='Times-Bold',
        spaceBefore=12,
        spaceAfter=12
    ))
    
    sender_info = """
    <font size="11">
    <b>Richard Adebayo &amp; Mercy Adebayo</b><br/>
    R. Augusto Costa 25<br/>
    1500-517 Lisboa, Portugal<br/>
    📞 +351 918 251 893<br/>
    📧 richard@moxx.co
    </font>
    """
    story.append(Paragraph(sender_info, styles['SenderAddress']))
    story.append(Spacer(1, 24))
    
    date_text = '<font size="11">Date: 22 October 2025</font>'
    story.append(Paragraph(date_text, styles['Normal']))
    story.append(Spacer(1, 24))
    
    divider = '<font size="11">⸻</font>'
    story.append(Paragraph(divider, styles['Normal']))
    story.append(Spacer(1, 12))
    
    recipient_info = """
    <font size="11">
    <b>To:</b><br/>
    Ms. Patricia<br/>
    Praceta Sporting Clube Lavradiense, 2<br/>
    Barreiro, Setúbal
    </font>
    """
    story.append(Paragraph(recipient_info, styles['Normal']))
    story.append(Spacer(1, 12))
    
    story.append(Paragraph(divider, styles['Normal']))
    story.append(Spacer(1, 24))
    
    subject = '<font size="11"><b>Subject: Rental Offer for 3-Bedroom Condominium</b></font>'
    story.append(Paragraph(subject, styles['Subject']))
    story.append(Spacer(1, 12))
    
    salutation = '<font size="11">Dear Ms. Patricia,</font>'
    story.append(Paragraph(salutation, styles['Normal']))
    story.append(Spacer(1, 12))
    
    para1 = """
    <font size="11">
    We, Richard Adebayo and Mercy Adebayo, are pleased to submit this offer to rent 
    your 3-bedroom condominium located at Praceta Sporting Clube Lavradiense, 2, 
    Barreiro, Setúbal.
    </font>
    """
    story.append(Paragraph(para1, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para2 = """
    <font size="11">
    After viewing and considering the property, we would like to proceed with the 
    following rental terms:
    </font>
    """
    story.append(Paragraph(para2, styles['Justify']))
    story.append(Spacer(1, 12))
    
    terms_data = [
        ['Contract Duration:', '24 months (2 years)'],
        ['Monthly Rent:', '€1,600'],
        ['Proposed Start Date:', '25 October 2025'],
        ['Payment Terms:', 'Rent payable monthly in advance'],
        ['Advance Payment:', '2 months\' rent (€3,200)'],
        ['Security Deposit:', '2 months\' rent (€3,200)'],
    ]
    
    terms_table = Table(terms_data, colWidths=[2.5*inch, 3.5*inch])
    terms_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Times-Roman'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('FONTNAME', (0, 0), (0, -1), 'Times-Bold'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    
    story.append(terms_table)
    story.append(Spacer(1, 12))
    
    para3 = """
    <font size="11">
    We understand that the total amount due prior to move-in will therefore be €6,400, 
    covering both the advance rent and the security deposit.
    </font>
    """
    story.append(Paragraph(para3, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para4 = """
    <font size="11">
    We are reliable and responsible tenants who will maintain the property with care 
    and respect. We are happy to provide our recent bank statement and company details 
    as part of the tenancy verification process.
    </font>
    """
    story.append(Paragraph(para4, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para5 = """
    <font size="11">
    Our business in Portugal operates under Dynotech Innovation, LDA, a subsidiary 
    of Moxx Technologies, with company address at:<br/>
    <b>Rua Luís de Camões 1017, 7° Dt°, Montijo 2870-154, Portugal</b><br/>
    🌐 https://dyno.pt
    </font>
    """
    story.append(Paragraph(para5, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para6 = """
    <font size="11">
    Should you have any counteroffer or adjustment you wish to propose, please feel 
    free to share it with us — we are open to discussing and reaching mutually 
    agreeable terms.
    </font>
    """
    story.append(Paragraph(para6, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para7 = """
    <font size="11">
    We look forward to your positive response and are ready to finalize the lease 
    agreement at your earliest convenience.
    </font>
    """
    story.append(Paragraph(para7, styles['Justify']))
    story.append(Spacer(1, 12))
    
    para8 = """
    <font size="11">
    Thank you very much for your time and consideration.
    </font>
    """
    story.append(Paragraph(para8, styles['Justify']))
    story.append(Spacer(1, 24))
    
    closing = """
    <font size="11">
    Kind regards,<br/><br/><br/>
    <b>Richard Adebayo</b><br/>
    <b>Mercy Adebayo</b><br/><br/>
    Dynotech Innovation, LDA<br/>
    <i>A subsidiary of Moxx Technologies</i><br/>
    🌐 https://dyno.pt
    </font>
    """
    story.append(Paragraph(closing, styles['Normal']))
    
    doc.build(story)
    
    print(f"✅ Professional rental offer letter created: {filename}")
    print(f"📄 File size: {os.path.getsize(filename) / 1024:.1f} KB")
    return filename

if __name__ == "__main__":
    create_rental_offer_pdf()
