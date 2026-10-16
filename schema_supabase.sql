-- Run in Supabase SQL Editor
create table if not exists public.student (
  id bigint generated always as identity primary key,
  license text unique not null,
  name text not null,
  birthdate text,
  gender text,
  classroom text,
  timeclass text,
  clup text,
  phonenumber text,
  belt text default 'Cấp 10',
  family text default 'Không',
  active text default 'Có',
  telegram_id text,
  created_at timestamptz default now()
);

create table if not exists public.hocphi (
  id bigint generated always as identity primary key,
  thoi_gian timestamptz default now(),
  ma_hv text references public.student(license) on update cascade on delete set null,
  ho_ten text,
  ngay_sinh text,
  gioi_tinh text,
  lop text,
  ca text,
  tong_tien integer default 0,
  thang_dong_phi text,
  ghi_chu text,
  ma_thang text,
  ma_quy text,
  chuyen_khoan text default 'TM'
);

create table if not exists public.ketqua (
  id bigint generated always as identity primary key,
  ky_thi text,
  ma_hv text references public.student(license) on update cascade on delete set null,
  cap_dai_thi text,
  so_thi text,
  ho_ten text,
  ngay_sinh text,
  gioi_tinh text,
  don_vi text,
  ten_clb text,
  tan text,
  luc text,
  tay text,
  chan text,
  tu_ve text,
  quyen text,
  phan_the text,
  song_dau text,
  the_luc text,
  ket_qua text,
  ghi_chu text,
  created_at timestamptz default now(),
  unique(ma_hv, cap_dai_thi, ky_thi)
);

create table if not exists public.thicap_ghichu (
  ma_quy text not null,
  ma_hv text not null references public.student(license) on update cascade on delete cascade,
  cap_du_thi text not null,
  ghi_chu text,
  primary key (ma_quy, ma_hv, cap_du_thi)
);

create table if not exists public.hoatdong (
  id bigint generated always as identity primary key,
  ma_hv text references public.student(license) on update cascade on delete set null,
  ho_ten text,
  ngay_sinh text,
  gioi_tinh text,
  don_vi text,
  hoat_dong text,
  thoi_gian text,
  dia_diem text,
  noi_dung text,
  ket_qua text
);

create table if not exists public.options (
  id bigint generated always as identity primary key,
  type text not null,
  value text not null,
  unique(type, value)
);

create index if not exists idx_student_license on public.student(license);
create index if not exists idx_student_name on public.student(name);
create index if not exists idx_hocphi_ma_hv on public.hocphi(ma_hv);
create index if not exists idx_hocphi_ma_thang on public.hocphi(ma_thang);
create index if not exists idx_hocphi_ma_quy on public.hocphi(ma_quy);

-- For first test only. Later, turn RLS on and create policies.
alter table public.student disable row level security;
alter table public.hocphi disable row level security;
alter table public.ketqua disable row level security;
alter table public.thicap_ghichu disable row level security;
alter table public.hoatdong disable row level security;
alter table public.options disable row level security;
