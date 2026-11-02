# ฟอนต์สำหรับเอกสาร

วางไฟล์ฟอนต์ไว้ในโฟลเดอร์นี้ **ก่อน build Docker image** (ไฟล์ฟอนต์ไม่ถูก commit เข้า git)

- `THSarabunIT๙.ttf`, `THSarabunIT๙ Bold.ttf`, `THSarabunIT๙ Italic.ttf`, `THSarabunIT๙ BoldItalic.ttf`
- `THSarabunNew.ttf`, `THSarabunNew Bold.ttf`, `THSarabunNew Italic.ttf`, `THSarabunNew BoldItalic.ttf`

รองรับนามสกุล `.ttf` และ `.otf` ชื่อไฟล์จะเป็นอะไรก็ได้

ถ้าไม่มีฟอนต์เหล่านี้ Docker image จะใช้ฟอนต์สำรอง "Sarabun Compat" แทนโดยอัตโนมัติ
- สร้างตอน build จากฟอนต์ Laksaman (TLWG, GPL-2 + font exception) โดยย่อขนาดตัวอักษรเหลือ 0.665 และระยะบรรทัดเหลือ 0.8 ให้ขนาดเท่า TH SarabunIT๙ (ดู `tools/make_fallback_font.py`)
- เลย์เอาต์ของหมายเรียกจึงใกล้เคียงกับที่พิมพ์จาก Word แต่รูปตัวอักษรและตัวเลขไทยต่างจาก IT๙ เล็กน้อย
- Laksaman ตัวเดิมตัวอักษรกว้างกว่า TH Sarabun ประมาณ 1.5 เท่า ห้ามใช้แทนตรงๆ เพราะป้ายจะตัดบรรทัดและหมายจะล้นเป็น 2 หน้า
