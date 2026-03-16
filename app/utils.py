# app/utils.py

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from datetime import datetime
import io
from datetime import date, timedelta
from .models import db, CreditGrant

def add_user_credit(user, amount, source_type, description, days_valid=30):
    """Creates a new credit record in the ledger."""
    expiry = date.today() + timedelta(days=days_valid)
    grant = CreditGrant(
        user_id=user.id,
        balance=amount,
        source_type=source_type,
        description=description,
        expiry_date=expiry
    )
    db.session.add(grant)
    db.session.commit()

def cleanup_user_expired_credits(user):
    """Removes expired credit grants from the database."""
    if not user.is_authenticated:
        return

    today = date.today()
    expired_grants = CreditGrant.query.filter(
        CreditGrant.user_id == user.id, 
        CreditGrant.expiry_date < today
    ).all()

    if expired_grants:
        count = len(expired_grants)
        for grant in expired_grants:
            db.session.delete(grant)

        try:
            db.session.commit()
            print(f"Cleaned up {count} expired credit records for {user.email}")
        except Exception as e:
            db.session.rollback()
            print(f"Error cleaning credits: {e}")

# --- Detailed PDF Waiver Generation 
def generate_detailed_waiver(user_data, signature_image, path):
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter  # Page dimensions

    # Helper to draw wrapped paragraphs
    def draw_paragraph(text_object, text_content, max_width):
        words = text_content.split()
        line = ''
        for word in words:
            # Use the text object's internal font state to calculate width
            if c.stringWidth(line + ' ' + word, text_object._fontname, text_object._fontsize) < max_width:
                line += ' ' + word
            else:
                text_object.textLine(line.strip())
                line = word
        text_object.textLine(line.strip())

    # --- PAGE 1 ---
    text = c.beginText(1 * inch, height - 1 * inch)
    text.setFont("Helvetica-Bold", 12)
    text.textLine("WAIVER, ASSUMPTION OF RISK,")
    text.textLine("RELEASE OF LIABILITY & INDEMNIFICATION AGREEMENT")
    text.textLine(" ")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("PLEASE READ THIS ENTIRE DOCUMENT CAREFULLY BEFORE SIGNING.")
    text.textLine("THIS IS A RELEASE OF LIABILITY AND WAIVER OF CERTAIN LEGAL RIGHTS.")
    text.textLine(" ")
    
    text.setFont("Helvetica", 9)
    text.setLeading(12)

    p1 = 'This Waiver, Assumption of Risk, Release of Liability & Indemnification Agreement (this "Agreement") is a binding agreement with Portland Public Volleyball LLC, an Oregon limited liability company (the "Company") required by the Company as a condition to participate in volleyball games, leagues or tournaments, volleyball instruction or coaching, or similar activities (the "Activities").'
    p2 = 'This Agreement must be signed by the person identified as the Participant below (the "Participant"), and if the Participant is not at least 18 years of age, this Agreement must also be signed by the Participant\'s parent or guardian. The Participant and the parent or guardian signing this Agreement are referred to below as the "Releasing Parties", or each individually, as a "Releasing Party".'
    p3 = "By signing this Agreement, each Releasing Party agrees that the waivers, releases, and other terms of this Agreement are binding on each Releasing Party, that the terms of this Agreement are a material condition on the Company's agreement to allow the Participant to engage in the Activities, and that the Company would not allow the Participant to participate in the Activities without the Releasing Parties' acceptance of the terms of this Agreement."
    p4 = "By signing this Agreement, each Releasing Party agrees that they have carefully read and understood this Agreement (including but not limited to the terms and conditions set forth in numbered sections 1 through 7 below). By signing below, each Releasing Party also agrees that this Agreement waives and releases certain legal rights that such Releasing Party would otherwise have, and that this Agreement is binding to the fullest extent permitted by law."

    draw_paragraph(text, p1, width - 2 * inch)
    text.textLine("")
    draw_paragraph(text, p2, width - 2 * inch)
    text.textLine("")
    draw_paragraph(text, p3, width - 2 * inch)
    text.textLine("")
    draw_paragraph(text, p4, width - 2 * inch)

    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("1. Assumption of Risks.")
    text.setFont("Helvetica", 9)
    p5 = "Each Releasing Party acknowledges and understands that THE ACTIVITIES CAN BE HAZARDOUS AND INVOLVES THE RISK OF PROPERTY DAMAGE, PHYSICAL INJURY AND/OR DEATH. Each Releasing Party understands the dangers and risks of the Activities and assumes all risks and dangers that are related to or may result from the Activities, including but not limited to: falling, slipping, tripping, loss of balance, collisions, injuries, Participant or another acting in a negligent or reckless manner that may cause and/or contribute to injury to Participant or others, storms and other adverse weather conditions, slick or uneven surfaces, holes, rocks, trees, marked and unmarked obstacles or dangers, equipment failure, equipment malfunction, equipment damage, Participant's improper use of equipment, limited access to and/or delay of medical attention, Participant's health condition, injuries that may result from strenuous activity, fatigue, exhaustion, dehydration, hypothermia, and mental distress from any of the above. Each Releasing Party acknowledges and understands that the description of the risks listed in the paragraph above are illustrative examples only and are not a complete list of potential risks and that the Activities may also include risks and dangers which are inherent and/or which cannot be reasonably avoided."
    p6 = "By signing this Agreement, each Releasing Party recognizes that property loss, injury, serious injury and death are all possible while participating in the Activities. EACH RELEASING PARTY AFFIRMS THAT HE OR SHE RECOGNIZES THE RISKS AND DANGERS, UNDERSTANDS THE DANGEROUS NATURE OF THE ACTIVITIES, VOLUNTARILY CHOOSES TO PARTICIPATE IN SUCH ACTIVITIES, AND EXPRESSLY ASSUMES ALL RISKS AND DANGERS OF THE ACTIVITIES, WHETHER OR NOT DESCRIBED ABOVE, KNOWN OR UNKNOWN, INHERENT OR OTHERWISE."
    
    draw_paragraph(text, p5, width - 2 * inch)
    text.textLine("")
    draw_paragraph(text, p6, width - 2 * inch)
    c.drawText(text)
    c.showPage() # END PAGE 1

    # --- PAGE 2 ---
    text = c.beginText(1 * inch, height - 1 * inch)
    text.setFont("Helvetica", 9)
    text.setLeading(12)

    text.setFont("Helvetica-Bold", 9)
    text.textLine("2. Covenant not to Sue.")
    text.setFont("Helvetica", 9)
    p7 = "THE RELEASING PARTIES EACH HEREBY AGREE NOT TO SUE THE COMPANY, the owner of the property or facilities where the Activities takes place, or any of their affiliates, successors in interest, affiliated organizations and companies, insurance carriers, agents, employees, representatives, assignees, officers, directors, shareholders, managers, members, partners and other related parties (each a \"Released Party\") for any injury or loss to Participant, including death, which Participant may suffer, arising in whole or in part out of Participant's participation in the Activities and for any property damage (including but not limited to equipment damage)."
    draw_paragraph(text, p7, width - 2 * inch)
    
    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("3. Release and Indemnity by Releasing Parties.")
    text.setFont("Helvetica", 9)
    p8 = "EACH RELEASING PARTY AGREES TO HOLD HARMLESS AND RELEASE EACH AND EVERY RELEASED PARTY FROM ANY AND ALL LIABILITY AND/OR CLAIMS FOR INJURY OR DEATH TO PERSONS OR DAMAGE TO PROPERTY ARISING FROM PARTICIPATION IN THE ACTIVITIES, INCLUDING, BUT NOT LIMITED TO, THOSE CLAIMS BASED ON ANY RELEASED PARTY'S ALLEGED OR ACTUAL NEGLIGENCE OR BREACH OF ANY CONTRACT AND/OR EXPRESS OR IMPLIED WARRANTY. By execution of this Agreement, each Released Party also AGREES TO DEFEND AND INDEMNIFY each Released Party from any and all claims of the Participant and/or a third party arising in whole or in part from Participant's participation in the Activities."
    draw_paragraph(text, p8, width - 2 * inch)
    
    # --- NEWLY ADDED SECTIONS ---
    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("4. No Assurances Regarding Locations, Facilities, or Equipment.")
    text.setFont("Helvetica", 9)
    p9 = "Each Releasing Party acknowledges that the Releasing Parties are solely responsible for determining that the locations, facilities and equipment are suitable and safe to be used for the Activities and for inspecting such locations, facilities and equipment to identify risks. Neither the Company nor any of the Released Parties have made any investigation or provided any assurances that the locations, facilities or equipment are suitable or safe for use in the Activities and have no duty of care or similar duty that would impose any obligation to do so. The Participant assumes all responsibility for any equipment provided by the Company and accepts full responsibility for its care and for all loss or damage that may be caused by or to the equipment, except reasonable wear and tear, while in Participant's possession."
    draw_paragraph(text, p9, width - 2 * inch)

    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("5. Abilities of Participant; Health Risks.")
    text.setFont("Helvetica", 9)
    p10 = "Each Releasing Party represents and warrants to the Released Parties that Participant has the physical ability and knowledge to safely engage in the Activities, and has made all appropriate inquiries of doctors or other medical professionals to confirm that Participant is healthy and capable of safely participating in the Activities without unacceptable health risks."
    draw_paragraph(text, p10, width - 2 * inch)
    
    # --- NEW SECTION ADDED ---
    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("6. Indemnification for Actions of Minors.")
    text.setFont("Helvetica", 9)
    p11_new = "Participant agrees that Participant is solely responsible for ensuring that any children of Participant or other minors present at the Activity at the invitation of Participant (a \"Minor Invitee\") is properly supervised and behaves in a manner that is safe and does not present a danger to the Minor Invitee or others. By execution of this Agreement, Participant also AGREES TO DEFEND AND INDEMNIFY each Released Party from any and all claims based on the actions or behavior of any Minor Invitee of such Participant at the Activity or the associated locations, facilities or use of any equipment."
    draw_paragraph(text, p11_new, width - 2 * inch)

    # --- RENUMBERED ---
    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("7. Miscellaneous.")
    text.setFont("Helvetica", 9)
    p12 = "If any part of this Agreement is deemed to be unenforceable, such term shall be modified to the minimum extent permitted or severed, and the remaining terms shall be an enforceable contract between the parties. It is the intent of Releasing Parties that this Agreement shall be binding upon the assignees, heirs, next of kin, executors and personal representatives of the Releasing Parties."
    draw_paragraph(text, p12, width - 2 * inch)
    
    # --- RENUMBERED ---
    text.textLine("")
    text.setFont("Helvetica-Bold", 9)
    text.textLine("8. Participant:")
    text.setFont("Helvetica", 9)
    text.textLine("Participant represents and warrants to the Releasing Parties that:")
    c.drawText(text)
    
    # The vertical offset (9.7) is increased to move the checkbox further down the page.
    box_x, box_y = 1.1 * inch, height - 9.7 * inch 
    c.rect(box_x, box_y, 10, 10)
    c.setFont("ZapfDingbats", 12)
    c.drawString(box_x + 1, box_y + 2, u'✔')
    c.setFont("Helvetica", 9)
    c.drawString(1.3 * inch, height - 9.68 * inch, "Participant is 18 years of age or older.")
    c.showPage() # END PAGE 2

    # --- PAGE 3: SIGNATURE PAGE ---
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1 * inch, height - 1.5 * inch, "SIGNATURE OF PARTICIPANT")
    
    text = c.beginText(1 * inch, height - 1.8 * inch)
    text.setFont("Helvetica", 9)
    text.setLeading(12)
    p12 = "By signing below, the Participant agrees to each of the terms of this Agreement and acknowledges that the Participant would not be permitted to participate in the Activities except on the terms set forth in this Agreement."
    draw_paragraph(text, p12, width - 2 * inch)
    c.drawText(text)

    image_reader = io.BytesIO(signature_image)
    c.drawImage(ImageReader(image_reader), 1 * inch, height - 3.5 * inch, width=150, height=50, mask='auto')

    text = c.beginText(1 * inch, height - 4 * inch)
    text.setFont("Helvetica", 11)
    text.setLeading(16)
    text.textLine("_____________________________")
    text.textLine(f"Participant Name: {user_data['name']}")
    text.textLine(f"Date: {datetime.now().strftime('%B %d, %Y')}")
    text.textLine(f"Mailing Address: {user_data['address']}")
    text.textLine(f"Date of Birth: {user_data['dob']}")
    text.textLine("")
    text.textLine(f"Emergency Contact Name: {user_data['emergency_name']}")
    text.textLine(f"Emergency Contact Phone Number: {user_data['emergency_phone']}")
    c.drawText(text)
    
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1*inch, height - 7 * inch, "SIGNATURE OF PARENT OR LEGAL GUARDIAN")
    c.drawString(1*inch, height - 7.2 * inch, "(Required if Participant is not 18 years old or older)")
    c.save()