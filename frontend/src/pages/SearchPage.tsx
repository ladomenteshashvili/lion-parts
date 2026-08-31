function SearchPage() {
  return (
    <section className="card">
      <p className="eyebrow">ნაწილების ძიება</p>
      <h1>მოძებნე ნაწილი part number-ით</h1>
      <p className="muted">
        შეიყვანე OEM part number. სურვილის შემთხვევაში დაამატე VIN, რომ ოპერატორმა თავსებადობა გადაამოწმოს.
      </p>

      <div className="search-form">
        <input placeholder="მაგ: 51118070648" />
        <input placeholder="VIN — არასავალდებულო" />
        <button>ძებნა</button>
      </div>
    </section>
  );
}

export default SearchPage;