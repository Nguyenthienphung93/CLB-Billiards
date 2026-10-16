BILLIARDS MANAGER - CÂU LẠC BỘ BILLIARDS THANH TRUNG

1. Cấu trúc:
   app.py
   .env
   requirements.txt
   templates/base.html
   templates/Thanhvien.html
   static/pictures/logo_clb.png   (hoặc .webp/.jpg/.jpeg/.svg)

2. Tạo môi trường và cài thư viện:
   pip install -r requirements.txt

3. Copy .env.example thành .env rồi điền:
   SUPABASE_URL
   SUPABASE_SERVICE_ROLE_KEY (khuyên dùng cho backend)

4. Đặt logo vào:
   static/pictures/logo_clb.png
   App tự dò png, webp, jpg, jpeg, svg theo tên logo_clb.

5. Chạy:
   python app.py

6. Mở:
   http://127.0.0.1:5050
   hoặc IP LAN, ví dụ http://192.168.x.x:5050

7. Bảng Supabase cần có tên chính xác: Thanhvien
   Các cột:
   id
   ma_thanh_vien
   ho_va_ten
   gioi_tinh
   ngay_sinh
   dia_chi
   so_dien_thoai
   created_at
   updated_at

Ghi chú:
- CSS được đặt trực tiếp trong từng template HTML, không có file CSS riêng.
- Đồng hồ cập nhật bằng setTimeout canh theo giây, không dùng vòng lặp nặng.
- Sidebar nhớ trạng thái mở/thu bằng localStorage.
